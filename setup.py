# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

packages = find_packages()


setup(
  name='twisted-telegram-bot',
  version="1.2.6",
  description="Asynchronous Twisted-based Telegram Bot API for Python",
  author='unintended',
  author_email='unintended.github@gmail.com',
  url='https://github.com/unintended/twisted-telegram-bot',
  license='MIT',
  packages=packages,
  install_requires=[
    'cachetools',
    'twisted',
    'treq',
  ]
)
