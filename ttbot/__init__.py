import json
import collections
import re
from itertools import groupby

import treq
import telegram
from cachetools import LRUCache
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, Deferred, DeferredList
from twisted.logger import Logger

from ttbot.types import Message, InlineQuery, ChosenInlineResult, JsonSerializable, CallbackQuery, File, ChannelPost

API_URL = r"https://api.telegram.org/"

log = Logger()

PM_MARKDOWN = 'markdown'


def is_string(var):
  return isinstance(var, basestring)


def is_command(text):
  return text.startswith('/')


def extract_command(text):
  return text.split()[0].split('@')[0][1:].lower() if is_command(text) else None


def _convert_utf8(data):
  if isinstance(data, str):
    return data
  if isinstance(data, unicode):
    return data.encode('utf-8')
  elif isinstance(data, collections.Mapping):
    return dict(map(_convert_utf8, data.iteritems()))
  elif isinstance(data, collections.Iterable):
    return type(data)(map(_convert_utf8, data))
  else:
    return data


@inlineCallbacks
def _check_response(resp, method_name):
  if resp.code != 200:
    error_text = yield resp.text()
    msg = 'The server returned HTTP {0} {1} ({2})'.format(resp.code, resp.phrase, error_text)
    raise ApiException(msg, method_name, resp)

  result_text = yield resp.text()

  try:
    result_json = json.loads(result_text)
  except:
    msg = 'The server returned an invalid JSON response. Response body:\n[{0}]'.format(result_text)
    raise ApiException(msg, method_name, resp)

  if not result_json['ok']:
    msg = 'Error code: {0} Description: {1}'.format(result_json['error_code'], result_json['description'])
    raise ApiException(msg, method_name, resp)

  returnValue(result_json)


def _convert_markup(reply_markup):
  if isinstance(reply_markup, JsonSerializable):
    return reply_markup.to_json()
  elif isinstance(reply_markup, dict):
    return json.dumps(reply_markup)


def _map_function_to_deferred(f, *args, **kwargs):
  rv = f(*args, **kwargs)
  if isinstance(rv, Deferred):
    return rv
  else:
    d = Deferred()
    d.callback(rv)
    return d


class TelegramBot(object):
  def __init__(self, token, name, skip_offset=False, allowed_updates=None, agent=None, timeout=None):
    self.name = name
    self.token = token
    self.agent = agent
    self.last_update_id = -2 if skip_offset else -1
    self.update_prehandlers = []
    self.message_handlers = []
    self.message_subscribers = LRUCache(maxsize=10000)
    self.message_prehandlers = []
    self.message_next_handlers = LRUCache(maxsize=1000)
    self.retry_update = 0
    self.allowed_updates = allowed_updates
    self.running = False
    self.inline_query_handler = None
    self.callback_query_handler = None
    self.chosen_inline_result_handler = None
    self.channel_post_handler = None
    self.on_updated_listener = None
    self.on_api_request_listener = None
    self.botan = None
    self.timeout = timeout
    self._noisy = False

  def method_url(self, method):
    return API_URL + 'bot' + self.token + '/' + method

  def start_update(self, default_delay=0, **kwargs):
    self.running = True

    @inlineCallbacks
    def update_bot():
      if not self.running:
        return

      try:
        yield self.get_update(**kwargs)

        self.retry_update = default_delay
      except:
        log.failure("Couldn't get updates. Delaying for {delay} seconds", delay=self.retry_update)
        self.retry_update = min(self.retry_update + 3, 20)
      reactor.callLater(self.retry_update, update_bot)

    reactor.callWhenRunning(update_bot)

  def stop_update(self):
    self.running = False

  @inlineCallbacks
  def get_update(self, telegram_timeout=10, timeout=None, limit=100):
    payload = {'timeout': telegram_timeout, 'offset': self.last_update_id + 1, 'limit': limit}
    if self.allowed_updates:
      payload['allowed_updates'] = self.allowed_updates
    updates = yield self._request('getUpdates', params=payload, timeout=timeout)

    if self.on_updated_listener:
      self.on_updated_listener(len(updates))

    max_update_id = -1
    inline_queries = []
    chosen_inline_results = []
    callback_queries = []
    channel_posts = []
    messages = []

    for update in updates:
      if self._noisy:
        log.debug("New update. ID: {update_id}", update_id=update['update_id'])
      self._notify_update_prehandlers(update)

      if 'inline_query' in update:
        inline_queries.append(InlineQuery.de_json(update['inline_query']))
      elif 'chosen_inline_result' in update:
        chosen_inline_results.append(ChosenInlineResult.de_json(update['chosen_inline_result']))
      elif 'callback_query' in update:
        callback_queries.append(CallbackQuery.de_json(update['callback_query']))
      elif 'channel_post' in update:
        channel_posts.append(ChannelPost(Message.de_json(update['channel_post'])))
      elif 'message' in update:
        msg = Message.de_json(update['message'])
        msg.bot_name = self.name  # FIXME: a hack
        messages.append(msg)
      else:
        log.debug("Unsupported update type: {update}",
                  update=json.dumps(update, skipkeys=True, ensure_ascii=False, default=lambda o: o.__dict__))

      if update['update_id'] > max_update_id:
        max_update_id = update['update_id']

    yield self.process_updates(inline_queries, chosen_inline_results, callback_queries, channel_posts, messages)

    self.last_update_id = max_update_id

  def process_updates(self, inline_queries, chosen_inline_results, callback_queries, channel_posts, messages):
    return DeferredList(
      [
        self.process_updates_parallel_with_handler(self.inline_query_handler, inline_queries, self),
        self.process_updates_parallel_with_handler(self.chosen_inline_result_handler, chosen_inline_results, self),

        # TODO: maybe callback_queries and channel_posts need to be processed one by one (is order important?)
        self.process_updates_parallel_with_handler(self.callback_query_handler, callback_queries, self),
        self.process_updates_parallel_with_handler(self.channel_post_handler, channel_posts, self),

        self.process_messages(messages)
      ]
    )

  @staticmethod
  def process_updates_parallel_with_handler(handler, updates, *args, **kwargs):
    if handler is not None and updates:
      return DeferredList([_map_function_to_deferred(handler, update, *args, **kwargs) for update in updates])
    else:
      d = Deferred()
      d.callback(None)
      return d

  def process_message(self, message):
    # synchronously notify prehandlers
    self._notify_message_prehandlers(message)

    message_subscriber_handler_function = self._find_message_subscriber_handler_function(message)
    if message_subscriber_handler_function is not None:
      return _map_function_to_deferred(message_subscriber_handler_function, message, self)

    message_next_handler = self._find_message_next_handler(message)
    if message_next_handler is not None:
      return _map_function_to_deferred(message_next_handler, message, self)

    command_handler_function = self._find_command_handler_function(message)
    if command_handler_function is not None:
      return _map_function_to_deferred(command_handler_function, message, self)

  @inlineCallbacks
  def process_messages_in_order(self, messages):
    for message in messages:
      yield self.process_message(message)

  def process_messages(self, messages):
    if messages:
      return DeferredList([self.process_messages_in_order(messages_group[1])
                           for messages_group
                           in groupby(sorted(messages, key=lambda m: m.chat.id), key=lambda m: m.chat.id)])
    else:
      d = Deferred()
      d.callback(None)
      return d

  def process_inline_query(self, inline_query):
    if self.inline_query_handler:
      self.inline_query_handler(inline_query, self)

  def process_chosen_inline_query(self, chosen_inline_result):
    if self.chosen_inline_result_handler:
      self.chosen_inline_result_handler(chosen_inline_result, self)

  def _notify_update_prehandlers(self, update):
    for handler in self.update_prehandlers:
      handler(update, self)

  def _notify_message_prehandlers(self, message):
    for handler in self.message_prehandlers:
      handler(message, self)

  def _find_command_handler_function(self, message):
    for message_handler in self.message_handlers:
      if self._test_message_handler(message_handler, message):
        return message_handler['function']
    return None

  def _find_message_subscriber_handler_function(self, message):
    if not hasattr(message, 'reply_to_message'):
      return None
    return self.message_subscribers.pop(message.reply_to_message.message_id, None)

  def _find_message_next_handler(self, message):
    return self.message_next_handlers.pop(message.chat.id, None)

  def register_message_handler(self, fn, commands=None, regexp=None, func=None, content_types=None):
    if not content_types:
      content_types = ['text']
    func_dict = {'function': fn, 'content_types': content_types}
    if regexp:
      func_dict['regexp'] = regexp if 'text' in content_types else None
    if func:
      func_dict['lambda'] = func
    if commands:
      func_dict['commands'] = commands if 'text' in content_types else None
    self.message_handlers.append(func_dict)

  def message_handler(self, commands=None, regexp=None, func=None, content_types=None):
    def decorator(fn):
      self.register_message_handler(fn, commands, regexp, func, content_types)
      return fn

    return decorator

  @staticmethod
  def _test_message_handler(message_handler, message):
    if message.content_type not in message_handler['content_types']:
      return False
    if 'commands' in message_handler and message.content_type == 'text':
      cmd = extract_command(message.text)
      if cmd:
        for command_pattern in message_handler['commands']:
          if not command_pattern.endswith('$'):
            command_pattern += '$'
          if re.match(command_pattern, cmd):
            return True
        return False
    if 'regexp' in message_handler \
        and message.content_type == 'text' \
        and re.search(message_handler['regexp'], message.text):
      return True
    if 'lambda' in message_handler:
      return message_handler['lambda'](message)
    return False

  @inlineCallbacks
  def send_message(self, chat_id, text,
                   disable_web_page_preview=None,
                   reply_to_message_id=None,
                   reply_markup=None,
                   parse_mode=None):
    method = r'sendMessage'

    payload = {'chat_id': str(chat_id), 'text': text}
    if disable_web_page_preview:
      payload['disable_web_page_preview'] = disable_web_page_preview
    if reply_to_message_id:
      payload['reply_to_message_id'] = reply_to_message_id
    if reply_markup:
      payload['reply_markup'] = _convert_markup(reply_markup)
    if parse_mode:
      payload['parse_mode'] = parse_mode
    request = yield self._request(method, 'POST', params=payload)
    returnValue(Message.de_json(request))

  @inlineCallbacks
  def answer_to_inline_query(self, query_id, results,
                             personal=False,
                             next_offset='',
                             switch_pm_text=None,
                             switch_pm_parameter=None):
    def _map_result(result):
      if isinstance(result, telegram.InlineQueryResult):
        return result.to_json()
      else:
        return json.dumps(result)

    payload = {
      'inline_query_id': str(query_id),
      'results': [_map_result(res) for res in results],
      'is_personal': personal,
      'next_offset': next_offset
    }
    if switch_pm_text:
      payload['switch_pm_text'] = switch_pm_text
    if switch_pm_parameter:
      payload['switch_pm_parameter'] = switch_pm_parameter
    request = yield self._request('answerInlineQuery', 'POST', params=payload)
    returnValue(request)

  @inlineCallbacks
  def edit_message_text(self, chat_id, message_id, text,
                        parse_mode=None,
                        disable_web_page_preview=None,
                        reply_markup=None):
    method = r'editMessageText'

    payload = {'chat_id': str(chat_id), 'message_id': str(message_id), 'text': text}
    if disable_web_page_preview:
      payload['disable_web_page_preview'] = disable_web_page_preview
    if reply_markup:
      if isinstance(reply_markup, JsonSerializable):
        payload['reply_markup'] = reply_markup.to_json()
      elif isinstance(reply_markup, dict):
        payload['reply_markup'] = json.dumps(reply_markup)
    if parse_mode:
      payload['parse_mode'] = parse_mode
    request = yield self._request(method, 'POST', params=payload)
    returnValue(Message.de_json(request))

  def set_webhook(self, url, certificate, max_connections=None):
    method = r'setWebhook'

    payload = {'url': url}
    files = None
    if not is_string(certificate):
      files = {'certificate': ('cert', certificate)}
    else:
      payload['certificate'] = certificate
    if max_connections:
      payload['max_connections'] = max_connections
    if self.allowed_updates:
      payload['allowed_updates'] = self.allowed_updates

    return self._make_request(method, 'POST', params=payload, files=files)

  def delete_webhook(self):
    method = r'deleteWebhook'

    return self._make_request(method, 'POST')

  @inlineCallbacks
  def delete_message(self, chat_id, message_id):
    method = r'deleteMessage'

    payload = {'chat_id': str(chat_id), 'message_id': str(message_id)}
    request = yield self._request(method, 'POST', params=payload)
    returnValue(request)

  @inlineCallbacks
  def answer_callback_query(self, callback_query_id,
                            text=None,
                            show_alert=None):
    method = r'answerCallbackQuery'

    payload = {'callback_query_id': str(callback_query_id)}
    if text:
      payload['text'] = text
    if show_alert:
      payload['show_alert'] = show_alert
    request = yield self._request(method, 'POST', params=payload)
    returnValue(request)

  @inlineCallbacks
  def get_file(self, file_id):
    method = r'getFile'

    payload = {'file_id': str(file_id)}

    request = yield self._request(method, 'POST', params=payload)
    returnValue(File.de_json(request))

  def get_file_url(self, file):
    return "https://api.telegram.org/file/bot%s/%s" % (self.token, file.path)

  @inlineCallbacks
  def send_audio(self, chat_id, audio,
                 filename='audio',
                 duration=None,
                 performer=None,
                 title=None,
                 caption=None,
                 reply_to_message_id=None,
                 reply_markup=None,
                 timeout=None):
    method = r'sendAudio'

    payload = {'chat_id': chat_id}
    files = None
    if not is_string(audio):
      files = {'audio': (filename, audio)}
    else:
      payload['audio'] = audio
    if duration:
      payload['duration'] = duration
    if performer:
      payload['performer'] = performer
    if title:
      payload['title'] = title
    if caption:
      payload['caption'] = caption
    if reply_to_message_id:
      payload['reply_to_message_id'] = reply_to_message_id
    if reply_markup:
      payload['reply_markup'] = _convert_markup(reply_markup)

    request = yield self._request(method, 'POST', params=payload, files=files, timeout=timeout)
    returnValue(Message.de_json(request))

  def reply_to(self, message, text, **kwargs):
    return self.send_message(message.chat.id, text, reply_to_message_id=message.message_id, **kwargs)

  def send_chat_action(self, chat_id, action):
    method = r'sendChatAction'

    payload = {'chat_id': chat_id, 'action': action}
    return self._make_request(method, 'POST', params=payload)

  def register_for_reply(self, message, callback):
    self.message_subscribers[message.message_id] = callback

  def register_next_chat_handler(self, chat_id, callback):
    self.message_next_handlers[chat_id] = callback

  @inlineCallbacks
  def _request(self, method_name, method='get', params=None, data=None, files=None, timeout=None, **kwargs):
    if self.on_api_request_listener:
      self.on_api_request_listener(method_name)
    result_json = yield self._make_request(method_name, method,
                                           params=params, data=data, files=files, timeout=timeout,
                                           **kwargs)
    returnValue(result_json['result'])

  @inlineCallbacks
  def _make_request(self, method_name, method='get', params=None, data=None, files=None, timeout=None, **kwargs):
    request_url = API_URL + 'bot' + self.token + '/' + method_name
    params = _convert_utf8(params)

    if timeout is None:
      timeout = self.timeout

    resp = yield treq.request(method, request_url, params=params, data=data, files=files, timeout=timeout,
                              agent=self.agent, **kwargs)
    result_json = yield _check_response(resp, method_name)
    returnValue(result_json)


class ApiException(Exception):
  def __init__(self, msg, method_name, result):
    super(ApiException, self).__init__("A request to the Telegram API was unsuccessful. {0}".format(msg))
    self.function_name = method_name
    self.result = result
