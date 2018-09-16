from unittest import TestCase
from mock import MagicMock, call

from ttbot import TelegramBot, Message
from ttbot.types import User


class TestTelegramBot(TestCase):
  def test_process_messages(self):
    bot = TelegramBot("ff", "botname")

    messages = [
      Message(1, None, None, User(1, None), None, {}),
      Message(2, None, None, User(2, None), None, {}),
      Message(3, None, None, User(2, None), None, {}),
      Message(4, None, None, User(3, None), None, {}),
      Message(5, None, None, User(1, None), None, {}),
      Message(6, None, None, User(2, None), None, {}),
      Message(7, None, None, User(3, None), None, {}),
      Message(8, None, None, User(1, None), None, {}),
      Message(9, None, None, User(1, None), None, {}),
      Message(10, None, None, User(3, None), None, {}),
    ]

    bot.process_message = MagicMock()
    bot.process_messages(messages)
    bot.process_message.assert_has_calls(
      [
        call(messages[0]),
        call(messages[4]),
        call(messages[7]),
        call(messages[8]),
        call(messages[1]),
        call(messages[2]),
        call(messages[5]),
        call(messages[3]),
        call(messages[6]),
        call(messages[9]),
      ]
    )
