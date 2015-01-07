#!/usr/bin/env python
""" Setup to allow pip installs of edx-search module """

from setuptools import setup

setup(
    name='edx-search',
    version='0.1.0',
    description='Search and Index routines for index access',
    author='edX',
    url='https://github.com/edx-solutions/edx-search',
    license='AGPL',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU Affero General Public License v3',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Framework :: Django',
    ],
    packages=['search'],
    dependency_links=[],
    install_requires=[]
)
