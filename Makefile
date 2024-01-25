.PHONY: clean quality requirements validate test quality-python install-local

clean:
	find . -name '__pycache__' -exec rm -rf {} +
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	coverage erase
	rm -rf coverage htmlcov
	rm -fr build/
	rm -fr dist/
	rm -fr *.egg-info

quality-python: ## Run python linters
	pycodestyle --config=.pep8 manage.py search edxsearch/settings.py setup.py
	pylint --rcfile=pylintrc manage.py search edxsearch/settings.py setup.py

quality: quality-python

requirements:
	pip install -qr requirements/pip.txt
	pip install -r requirements/dev.txt

validate: clean
	tox

test.up_elasticsearch:
	docker-compose up test_elasticsearch -d

test.down_elasticsearch:
	docker-compose down test_elasticsearch

test_with_es: clean test.up_elasticsearch
	coverage run --source='.' manage.py test
	make test.down_elasticsearch

test.up_opensearch:
	docker-compose up test_opensearch -d

test.down_opensearch:
	docker-compose down test_opensearch

test_with_os: clean test.up_opensearch
	coverage run --source='.' manage.py test
	make test.down_opensearch

upgrade: export CUSTOM_COMPILE_COMMAND=make upgrade
upgrade: ## update the requirements/*.txt files with the latest packages satisfying requirements/*.in
	pip install -qr requirements/pip-tools.txt
	# Make sure to compile files after any other files they include!
	pip-compile --rebuild --allow-unsafe --rebuild -o requirements/pip.txt requirements/pip.in
	pip-compile --rebuild --upgrade -o requirements/pip-tools.txt requirements/pip-tools.in
	pip install -qr requirements/pip.txt
	pip install -qr requirements/pip-tools.txt
	pip-compile --rebuild --upgrade -o requirements/base.txt requirements/base.in
	pip-compile --rebuild --upgrade -o requirements/testing.txt requirements/testing.in
	pip-compile --rebuild --upgrade -o requirements/quality.txt requirements/quality.in
	pip-compile --rebuild --upgrade -o requirements/ci.txt requirements/ci.in
	pip-compile --rebuild --upgrade -o requirements/dev.txt requirements/dev.in
	# Let tox control the Django version for tests
	sed '/^[dD]jango==/d' requirements/testing.txt > requirements/testing.tmp
	mv requirements/testing.tmp requirements/testing.txt

test: test_with_es test_with_os ## run tests and generate coverage report

install-local: ## installs your local edx-search into the LMS and CMS python virtualenvs
	docker exec -t edx.devstack.lms bash -c '. /edx/app/edxapp/venvs/edxapp/bin/activate && cd /edx/app/edxapp/edx-platform && pip uninstall -y edx-search && pip install -e /edx/src/edx-search && pip freeze | grep edx-search'
	docker exec -t edx.devstack.cms bash -c '. /edx/app/edxapp/venvs/edxapp/bin/activate && cd /edx/app/edxapp/edx-platform && pip uninstall -y edx-search && pip install -e /edx/src/edx-search && pip freeze | grep edx-search'
