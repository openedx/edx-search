#!/usr/bin/env python
""" Setup to allow pip installs of edx-search module """

from setuptools import setup


def load_requirements(*requirements_paths):
    """
    Load all requirements from the specified requirements files.
    Returns:
        list: Requirements file relative path strings
    """
    requirements = set()
    for path in requirements_paths:
        requirements.update(
            line.split('#')[0].strip() for line in open(path).readlines()
            if is_requirement(line.strip())
        )
    return list(requirements)


def is_requirement(line):
    """
    Return True if the requirement line is a package requirement.
    Returns:
        bool: True if the line is not blank, a comment, a URL, or an included file
    """
    return line and not line.startswith(('-r', '#', '-e', 'git+', '-c'))


setup(
    name='edx-search',
    version='2.0.1',
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
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.8',
        'Framework :: Django',
        'Framework :: Django :: 2.2',
    ],
    packages=['search', 'search.tests'],
    install_requires=load_requirements('requirements/base.in')
)
