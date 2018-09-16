import json


class JsonSerializable(object):
  def to_json(self, ensure_ascii=False):
    return json.dumps(self.to_json_dict(), ensure_ascii=ensure_ascii)

  def to_json_dict(self):
    raise NotImplementedError


class JsonDeserializable(object):
  @classmethod
  def de_json(cls, json_string):
    raise NotImplementedError

  @staticmethod
  def check_json(json_obj):
    if type(json_obj) == dict:
      return json_obj
    elif type(json_obj) in [str, unicode]:
      return json.loads(json_obj)
    else:
      raise ValueError("Invalid json type: %s" % type(json_obj))


class File(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    return File(obj['file_id'], obj.get('file_size', 0), obj.get('file_path'))

  def __init__(self, id, size, path):
    self.id = id
    self.size = size
    self.path = path


class User(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    id = obj['id']
    first_name = obj.get('first_name')
    username = obj.get('username')
    last_name = obj.get('last_name')
    language_code = obj.get('language_code')
    return User(id, first_name, last_name, username, language_code)

  def __init__(self, id, first_name, last_name=None, username=None, language_code=None):
    self.id = id
    self.first_name = first_name
    self.username = username
    self.last_name = last_name
    self.type = 'private'
    self.language_code = language_code


class Message(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    message_id = obj['message_id']
    from_user = None
    if 'from' in obj.keys():
      from_user = User.de_json(obj['from'])
    chat = Message.parse_chat(obj['chat'])
    date = obj['date']
    content_type = None
    opts = {}
    if 'forward_from' in obj:
      opts['forward_from'] = User.de_json(obj['forward_from'])
    if 'forward_date' in obj:
      opts['forward_date'] = obj['forward_date']
    if 'reply_to_message' in obj:
      opts['reply_to_message'] = Message.de_json(obj['reply_to_message'])
    if 'text' in obj:
      opts['text'] = " ".join(obj['text'].split())
      content_type = 'text'
    if 'audio' in obj:
      opts['audio'] = Audio.de_json(obj['audio'])
      content_type = 'audio'
    if 'voice' in obj:
      opts['voice'] = Audio.de_json(obj['voice'])
      content_type = 'voice'
    if 'document' in obj:
      opts['document'] = Document.de_json(obj['document'])
      content_type = 'document'
    if 'photo' in obj:
      opts['photo'] = Message.parse_photo(obj['photo'])
      content_type = 'photo'
    if 'sticker' in obj:
      opts['sticker'] = Sticker.de_json(obj['sticker'])
      content_type = 'sticker'
    if 'video' in obj:
      opts['video'] = Video.de_json(obj['video'])
      content_type = 'video'
    if 'location' in obj:
      opts['location'] = Location.de_json(obj['location'])
      content_type = 'location'
    if 'contact' in obj:
      opts['contact'] = Contact.de_json(json.dumps(obj['contact']))
      content_type = 'contact'
    if 'new_chat_participant' in obj:
      opts['new_chat_participant'] = User.de_json(obj['new_chat_participant'])
      content_type = 'new_chat_participant'
    if 'left_chat_participant' in obj:
      opts['left_chat_participant'] = User.de_json(obj['left_chat_participant'])
      content_type = 'left_chat_participant'
    if 'new_chat_title' in obj:
      opts['new_chat_title'] = obj['new_chat_title']
      content_type = 'new_chat_title'
    if 'new_chat_photo' in obj:
      opts['new_chat_photo'] = obj['new_chat_photo']
      content_type = 'new_chat_photo'
    if 'delete_chat_photo' in obj:
      opts['delete_chat_photo'] = obj['delete_chat_photo']
      content_type = 'delete_chat_photo'
    if 'group_chat_created' in obj:
      opts['group_chat_created'] = obj['group_chat_created']
      content_type = 'group_chat_created'
    if 'caption' in obj:
      opts['caption'] = obj['caption']
    return Message(message_id, from_user, date, chat, content_type, opts)

  @classmethod
  def parse_chat(cls, chat):
    if chat['type'] != 'private':
      return GroupChat.de_json(chat)
    else:
      return User.de_json(chat)

  @classmethod
  def parse_photo(cls, photo_size_array):
    ret = []
    for ps in photo_size_array:
      ret.append(PhotoSize.de_json(ps))
    return ret

  def __init__(self, message_id, from_user, date, chat, content_type, options):
    self.chat = chat
    self.date = date
    self.from_user = from_user
    self.message_id = message_id
    self.content_type = content_type
    self.bot_name = None
    for key in options:
      setattr(self, key, options[key])

  def __repr__(self):
    return "Message #%d" % self.message_id


class PhotoSize(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    file_id = obj['file_id']
    width = obj['width']
    height = obj['height']
    file_size = obj.get('file_size')
    return PhotoSize(file_id, width, height, file_size)

  def __init__(self, file_id, width, height, file_size=None):
    self.file_size = file_size
    self.height = height
    self.width = width
    self.file_id = file_id


class Audio(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    file_id = obj['file_id']
    duration = obj['duration']
    performer = obj.get('performer')
    title = obj.get('title')
    mime_type = obj.get('mime_type')
    file_size = obj.get('file_size')
    return Audio(file_id, duration, performer, title, mime_type, file_size)

  def __init__(self, file_id, duration, performer=None, title=None, mime_type=None, file_size=None):
    self.file_id = file_id
    self.duration = duration
    self.performer = performer
    self.title = title
    self.mime_type = mime_type
    self.file_size = file_size


class Voice(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    file_id = obj['file_id']
    duration = obj['duration']
    mime_type = None
    file_size = None
    if 'mime_type' in obj:
      mime_type = obj['mime_type']
    if 'file_size' in obj:
      file_size = obj['file_size']
    return Voice(file_id, duration, mime_type, file_size)

  def __init__(self, file_id, duration, mime_type=None, file_size=None):
    self.file_id = file_id
    self.duration = duration
    self.mime_type = mime_type
    self.file_size = file_size


class InlineQuery(JsonDeserializable):
  def __init__(self, query_id, from_user, query, offset):
    self.query_id = query_id
    self.from_user = from_user
    self.query = query
    self.offset = offset

  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    query_id = obj['id']
    from_user = User.de_json(obj['from'])
    query = obj['query']
    offset = obj['offset']
    return InlineQuery(query_id, from_user, query, offset)


class ChosenInlineResult(JsonDeserializable):
  def __init__(self, result_id, from_user, query):
    self.result_id = result_id
    self.from_user = from_user
    self.query = query

  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    result_id = obj['result_id']
    from_user = User.de_json(obj['from'])
    query = obj['query']
    return ChosenInlineResult(result_id, from_user, query)


class CallbackQuery(JsonDeserializable):
  def __init__(self, query_id, from_user, data, message, inline_message_id):
    self.query_id = query_id
    self.from_user = from_user
    self.data = data
    self.message = message
    self.inline_message_id = inline_message_id

  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    from_user = User.de_json(obj['from'])
    message = None
    inline_message_id = None
    if 'message' in obj.keys():
      message = Message.de_json(obj['message'])
    elif 'inline_message_id' in obj.keys():
      inline_message_id = obj['inline_message_id']
    return CallbackQuery(obj['id'], from_user, obj['data'], message, inline_message_id)


class Document(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    file_id = obj['file_id']
    thumb = None
    if 'thumb' in obj:
      if 'file_id' in obj['thumb']:
        thumb = PhotoSize.de_json(obj['thumb'])
    file_name = None
    mime_type = None
    file_size = None
    if 'file_name' in obj:
      file_name = obj['file_name']
    if 'mime_type' in obj:
      mime_type = obj['mime_type']
    if 'file_size' in obj:
      file_size = obj['file_size']
    return Document(file_id, thumb, file_name, mime_type, file_size)

  def __init__(self, file_id, thumb, file_name=None, mime_type=None, file_size=None):
    self.file_id = file_id
    self.thumb = thumb
    self.file_name = file_name
    self.mime_type = mime_type
    self.file_size = file_size


class Sticker(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    file_id = obj['file_id']
    width = obj['width']
    height = obj['height']
    thumb = None
    if 'thumb' in obj:
      thumb = PhotoSize.de_json(obj['thumb'])
    file_size = None
    if 'file_size' in obj:
      file_size = obj['file_size']
    return Sticker(file_id, width, height, thumb, file_size)

  def __init__(self, file_id, width, height, thumb, file_size=None):
    self.file_id = file_id
    self.width = width
    self.height = height
    self.thumb = thumb
    self.file_size = file_size


class Video(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    file_id = obj['file_id']
    width = obj['width']
    height = obj['height']
    duration = obj['duration']
    thumb = None
    mime_type = None
    file_size = None
    if 'thumb' in obj:
      thumb = PhotoSize.de_json(obj['thumb'])
    if 'mime_type' in obj:
      mime_type = obj['mime_type']
    if 'file_size' in obj:
      file_size = obj['file_size']
    return Video(file_id, width, height, duration, thumb, mime_type, file_size)

  def __init__(self, file_id, width, height, duration, thumb=None, mime_type=None, file_size=None):
    self.file_id = file_id
    self.width = width
    self.height = height
    self.duration = duration
    self.thumb = thumb
    self.mime_type = mime_type
    self.file_size = file_size


class Contact(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    phone_number = obj['phone_number']
    first_name = obj['first_name']
    last_name = None
    user_id = None
    if 'last_name' in obj:
      last_name = obj['last_name']
    if 'user_id' in obj:
      user_id = obj['user_id']
    return Contact(phone_number, first_name, last_name, user_id)

  def __init__(self, phone_number, first_name, last_name=None, user_id=None):
    self.phone_number = phone_number
    self.first_name = first_name
    self.last_name = last_name
    self.user_id = user_id


class Location(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    longitude = obj['longitude']
    latitude = obj['latitude']
    return Location(longitude, latitude)

  def __init__(self, longitude, latitude):
    self.longitude = longitude
    self.latitude = latitude


class UserProfilePhotos(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    total_count = obj['total_count']
    photos = [[PhotoSize.de_json(y) for y in x] for x in obj['photos']]
    return UserProfilePhotos(total_count, photos)

  def __init__(self, total_count, photos):
    self.total_count = total_count
    self.photos = photos


class GroupChat(JsonDeserializable):
  @classmethod
  def de_json(cls, json_string):
    obj = cls.check_json(json_string)
    id = obj['id']
    title = obj['title']
    type = obj['type']
    return GroupChat(id, title, type)

  def __init__(self, id, title, type):
    self.id = id
    self.title = title
    self.type = type


class InlineKeyboardMarkup(JsonSerializable):
  def to_json_dict(self):
    return {'inline_keyboard': [[button.to_json_dict() for button in row] for row in self.buttons]}

  def __init__(self, buttons):
    self.buttons = buttons


class InlineKeyboardButton(JsonSerializable):
  def to_json_dict(self):
    return {k: v for k, v in self.__dict__.items() if v is not None}

  def __init__(self, text, url=None, callback_data=None, switch_inline_query=None):
    super(InlineKeyboardButton, self).__init__()
    self.text = text
    self.url = url
    self.callback_data = callback_data
    self.switch_inline_query = switch_inline_query


class ChannelPost:
  def __init__(self, message):
    self.message = message