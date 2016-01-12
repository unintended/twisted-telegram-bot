# -*- coding: utf-8 -*-
import os

from setuptools import setup, find_packages
from setuptools.command.develop import develop
from setuptools.command.install import install

packages = find_packages()


setup(
  name='twisted-telegram-bot',
  version=0.1,
  description="Asynchronous Twisted-based Telegram Bot API for Python",
  author='unintended',
  author_email='unintended.github@gmail.com',
  packages=packages,
  install_requires=[
    'twisted',
    'treq',
  ]
)
