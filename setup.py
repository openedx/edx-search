#!/usr/bin/env python
""" Setup to allow pip installs of edx-search module """

from __future__ import absolute_import
from setuptools import setup

setup(
    name='edx-search',
    version='1.2.2',
    description='Search and index routines for index access',
    author='edX',
    author_email='oscm@edx.org',
    url='https://github.com/edx/edx-search',
    license='AGPL',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 2',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Framework :: Django',
        'Framework :: Django :: 1.11',
        'Framework :: Django :: 2.0',
        'Framework :: Django :: 2.1',
    ],
    packages=['search', 'search.tests'],
    install_requires=[
        "django >= 1.8, < 2.0",
        "elasticsearch>=1.0.0,<2.0.0"
    ]
)
