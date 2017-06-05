import json
import collections
import re

import treq
from cachetools import LRUCache
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.logger import Logger
from twisted.web.client import Agent

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
def _make_request(token, method_name, method='get', params=None, data=None, files=None, timeout=10, **kwargs):
  request_url = API_URL + 'bot' + token + '/' + method_name
  params = _convert_utf8(params)

  resp = yield treq.request(method, request_url, params=params, data=data, files=files, timeout=timeout, **kwargs)
  result_json = yield _check_response(resp, method_name)
  returnValue(result_json)


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


@inlineCallbacks
def _request(token, method_name, method='get', params=None, data=None, files=None, timeout=10, **kwargs):
  result_json = yield _make_request(token, method_name, method,
                                    params=params, data=data, files=files, timeout=timeout,
                                    **kwargs)
  returnValue(result_json['result'])


def _convert_markup(reply_markup):
  if isinstance(reply_markup, JsonSerializable):
    return reply_markup.to_json()
  elif isinstance(reply_markup, dict):
    return json.dumps(reply_markup)


class TelegramBot:
  def __init__(self, token, name, skip_offset=False, allowed_updates=None):
    self.name = name
    self.token = token
    self.agent = Agent(reactor)
    self.last_update_id = -2 if skip_offset else -1
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
    self.botan = None

  def method_url(self, method):
    return API_URL + 'bot' + self.token + '/' + method

  def start_update(self):
    self.running = True

    @inlineCallbacks
    def update_bot():
      if not self.running:
        return

      try:
        yield self.get_update()

        self.retry_update = 0
        reactor.callWhenRunning(update_bot)
      except:
        log.failure("Couldn't get updates. Delaying for %d seconds" % self.retry_update)
        reactor.callLater(self.retry_update, update_bot)
        self.retry_update = min(self.retry_update + 3, 20)

    reactor.callWhenRunning(update_bot)

  def stop_update(self):
    self.running = False

  @inlineCallbacks
  def get_update(self, timeout=20):
    payload = {'timeout': timeout, 'offset': self.last_update_id + 1}
    if self.allowed_updates:
      payload['allowed_updates'] = self.allowed_updates
    updates = yield _request(self.token, 'getUpdates', params=payload, timeout=25)

    new_messages = []
    for update in updates:
      log.debug("New update. ID: {update_id}", update_id=update['update_id'])
      if update['update_id'] > self.last_update_id:
        self.last_update_id = update['update_id']

      if 'inline_query' in update.keys():
        inline_query = InlineQuery.de_json(update['inline_query'])
        self.process_inline_query(inline_query)
      elif 'chosen_inline_result' in update.keys():
        chosen_inline_result = ChosenInlineResult.de_json(update['chosen_inline_result'])
        self.process_chosen_inline_query(chosen_inline_result)
      elif 'message' in update.keys():
        msg = Message.de_json(update['message'])
        msg.bot_name = self.name
        new_messages.append(msg)
      elif 'callback_query' in update.keys():
        callback_query = CallbackQuery.de_json(update['callback_query'])
        self.process_callback_query(callback_query)
      elif 'channel_post' in update.keys():
        self.process_channel_post(ChannelPost(Message.de_json(update['channel_post'])))
      else:
        log.debug("Unknown update type: {update}",
                  update=json.dumps(update, skipkeys=True, ensure_ascii=False, default=lambda o: o.__dict__))

    if len(new_messages) > 0:
      self.process_new_messages(new_messages)

  def process_channel_post(self, channel_post):
    if self.channel_post_handler:
      self.channel_post_handler(channel_post, self)

  def process_callback_query(self, callback_query):
    if self.callback_query_handler:
      self.callback_query_handler(callback_query, self)

  def process_new_messages(self, new_messages):
    self._notify_message_prehandlers(new_messages)

    not_processed = []
    for message in new_messages:
      if not self._notify_message_next_handler(message):
        not_processed.append(message)
    new_messages = not_processed

    self._notify_command_handlers(new_messages)
    self._notify_message_subscribers(new_messages)

  def process_inline_query(self, inline_query):
    if self.inline_query_handler:
      self.inline_query_handler(inline_query, self)

  def process_chosen_inline_query(self, chosen_inline_result):
    if self.chosen_inline_result_handler:
      self.chosen_inline_result_handler(chosen_inline_result, self)

  def _notify_message_prehandlers(self, new_messages):
    for message in new_messages:
      for handler in self.message_prehandlers:
        handler(message, self)

  def _notify_command_handlers(self, new_messages):
    for message in new_messages:
      for message_handler in self.message_handlers:
        if self._test_message_handler(message_handler, message):
          message_handler['function'](message, self)
          break

  def _notify_message_subscribers(self, new_messages):
    for message in new_messages:
      if not hasattr(message, 'reply_to_message'):
        continue

      handler = self.message_subscribers.pop(message.reply_to_message.message_id, None)
      if handler is not None:
        handler(message, self)

  def _notify_message_next_handler(self, message):
    handler = self.message_next_handlers.pop(message.chat.id, None)
    if handler is not None:
      handler(message, self)
      return True
    return False

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
    request = yield _request(self.token, method, 'POST', params=payload)
    returnValue(Message.de_json(request))

  @inlineCallbacks
  def answer_to_inline_query(self, query_id, results, personal=False, next_offset=''):
    request = yield _request(self.token, 'answerInlineQuery', 'POST', params={
      'inline_query_id': str(query_id),
      'results': json.dumps(results, ensure_ascii=False),
      'is_personal': personal,
      'next_offset': next_offset
    })
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
    request = yield _request(self.token, method, 'POST', params=payload)
    returnValue(Message.de_json(request))

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
    request = yield _request(self.token, method, 'POST', params=payload)
    returnValue(request)

  @inlineCallbacks
  def get_file(self, file_id):
    method = r'getFile'

    payload = {'file_id': str(file_id)}

    request = yield _request(self.token, method, 'POST', params=payload)
    returnValue(File.de_json(request))


  def get_file_url(self, file):
    return "https://api.telegram.org/file/bot%s/%s" % (self.token, file.path)


  @inlineCallbacks
  def send_audio(self, chat_id, audio,
                 filename='audio',
                 duration=None,
                 performer=None,
                 title=None,
                 reply_to_message_id=None,
                 reply_markup=None,
                 timeout=30):
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
    if reply_to_message_id:
      payload['reply_to_message_id'] = reply_to_message_id
    if reply_markup:
      payload['reply_markup'] = _convert_markup(reply_markup)

    request = yield _request(self.token, method, 'POST', params=payload, files=files, timeout=timeout)
    returnValue(Message.de_json(request))

  def reply_to(self, message, text, **kwargs):
    return self.send_message(message.chat.id, text, reply_to_message_id=message.message_id, **kwargs)

  def send_chat_action(self, chat_id, action):
    method = r'sendChatAction'

    payload = {'chat_id': chat_id, 'action': action}
    return _make_request(self.token, method, 'POST', params=payload)

  def register_for_reply(self, message, callback):
    self.message_subscribers[message.message_id] = callback

  def register_next_chat_handler(self, chat_id, callback):
    self.message_next_handlers[chat_id] = callback


class TelegramBots:
  def __init__(self, bots):
    self.bots = bots

  def start_update(self):
    for bot in self.bots:
      bot.start_update()

  def stop_update(self):
    for bot in self.bots:
      bot.stop_update()

  def message_handler(self, commands=None, regexp=None, func=None, content_types=None):
    def decorator(fn):
      for bot in self.bots:
        bot.register_message_handler(fn, commands, regexp, func, content_types)
      return fn

    return decorator


class ApiException(Exception):
  def __init__(self, msg, method_name, result):
    super(ApiException, self).__init__("A request to the Telegram API was unsuccessful. {0}".format(msg))
    self.function_name = method_name
    self.result = result
