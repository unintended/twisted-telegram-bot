import json
import collections
import re

import treq
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.logger import Logger
from twisted.web.client import Agent

from ttbot.types import Message, InlineQuery, ChosenInlineResult

API_URL = r"https://api.telegram.org/"

log = Logger()


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
def _make_request(token, method_name, method='get', params=None, data=None, files=None, **kwargs):
  request_url = API_URL + 'bot' + token + '/' + method_name
  params = _convert_utf8(params)

  resp = yield treq.request(method, request_url, params=params, data=data, files=files, **kwargs)
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
def _request(token, method_name, method='get', params=None, data=None, files=None, **kwargs):
  result_json = yield _make_request(token, method_name, method, params=params, data=data, files=files, **kwargs)
  returnValue(result_json['result'])


class TelegramBot:
  def __init__(self, token, name):
    self.name = name
    self.token = token
    self.agent = Agent(reactor)
    self.last_update_id = -1
    self.message_handlers = []
    self.message_subscribers_messages = []
    self.message_subscribers_callbacks = []
    self.message_prehandlers = []
    self.message_next_handlers = {}
    self.retry_update = 0
    self.running = False
    self.inline_query_handler = None
    self.chosen_inline_result_handler = None

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
  def get_update(self):
    payload = {'timeout': 20, 'offset': self.last_update_id + 1}
    updates = yield _request(self.token, 'getUpdates', params=payload, timeout=25)

    new_messages_ids = set()
    new_messages = []
    new_inline_queries = []
    new_chosen_inline_results = []
    for update in updates:
      log.debug("New update. ID: {update_id}", update_id=update['update_id'])
      if update['update_id'] > self.last_update_id:
        self.last_update_id = update['update_id']

      if 'inline_query' in update.keys():
        inline_query = InlineQuery.de_json(update['inline_query'])
        new_inline_queries.append(inline_query)
      elif 'chosen_inline_result' in update.keys():
        chosen_inline_result = ChosenInlineResult.de_json(update['chosen_inline_result'])
        new_chosen_inline_results.append(chosen_inline_result)
      elif 'message' in update.keys():
        msg = Message.de_json(update['message'])
        try:
          log.debug("{user}: {msg}", user=msg.from_user.id, msg=msg.text)
        except AttributeError:
          log.debug("Empty message: {msg}",
                    msg=json.dumps(msg, skipkeys=True, ensure_ascii=False, default=lambda o: o.__dict__))
        if not msg.from_user.id in new_messages_ids:
          new_messages.append(msg)
          new_messages_ids.add(msg.from_user.id)
      else:
        log.debug("Unknown update type: {update}",
                  update=json.dumps(update, skipkeys=True, ensure_ascii=False, default=lambda o: o.__dict__))

    if len(new_messages) > 0:
      self.process_new_messages(new_messages)

    for inline_query in new_inline_queries:
      self.process_inline_query(inline_query)

    for chosen_inline_result in new_chosen_inline_results:
      self.process_chosen_inline_query(chosen_inline_result)

  def process_new_messages(self, new_messages):
    not_processed = []
    for message in new_messages:
      if not self._notify_message_next_handler(message):
        not_processed.append(message)
    new_messages = not_processed

    self._notify_message_prehandlers(new_messages)
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

      reply_msg_id = message.reply_to_message.message_id
      if reply_msg_id in self.message_subscribers_messages:
        index = self.message_subscribers_messages.index(reply_msg_id)
        self.message_subscribers_callbacks[index](message, self)

        del self.message_subscribers_messages[index]
        del self.message_subscribers_callbacks[index]

  def _notify_message_next_handler(self, message):
    if self.message_next_handlers.has_key(message.chat.id):
      self.message_next_handlers.pop(message.chat.id)(message)
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
    cmd = extract_command(message.text)
    if cmd:
      if 'commands' in message_handler and message.content_type == 'text':
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
      payload['reply_markup'] = json.dumps(reply_markup)
    if parse_mode:
      payload['parse_mode'] = parse_mode
    request = yield _request(self.token, method, 'POST', params=payload)
    returnValue(Message.de_json(request))

  @inlineCallbacks
  def answer_to_inline_query(self, query_id, results, personal=False):
    request = yield _request(self.token, 'answerInlineQuery', 'POST', params={
      'inline_query_id': str(query_id),
      'results': json.dumps(results, ensure_ascii=False),
      'is_personal': personal
    })
    returnValue(request)

  @inlineCallbacks
  def send_audio(self, chat_id, audio,
                 filename='audio',
                 duration=None,
                 performer=None,
                 title=None,
                 reply_to_message_id=None,
                 reply_markup=None):
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
    # if reply_markup:
    #   payload['reply_markup'] = _convert_markup(reply_markup)

    request = yield _request(self.token, method, 'POST', params=payload, files=files)
    returnValue(Message.de_json(request))

  def reply_to(self, message, text, **kwargs):
    return self.send_message(message.chat.id, text, reply_to_message_id=message.message_id, **kwargs)

  def send_chat_action(self, chat_id, action):
    method = r'sendChatAction'

    payload = {'chat_id': chat_id, 'action': action}
    return _make_request(self.token, method, 'POST', params=payload)

  def register_for_reply(self, message, callback):
    self.message_subscribers_messages.insert(0, message.message_id)
    self.message_subscribers_callbacks.insert(0, callback)
    if len(self.message_subscribers_messages) > 10000:
      self.message_subscribers_messages.pop()
      self.message_subscribers_callbacks.pop()

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
