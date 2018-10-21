# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

packages = find_packages()


setup(
  name='twisted-telegram-bot',
  version="2.1.0",
  description="Asynchronous Twisted-based Telegram Bot API for Python",
  author='unintended',
  author_email='unintended.github@gmail.com',
  url='https://github.com/unintended/twisted-telegram-bot',
  license='MIT',
  packages=packages,
  install_requires=[
    'python-telegram-bot',
    'cachetools',
    'twisted',
    'treq',
  ],
  tests_require=[
    'mock'
  ]
)
