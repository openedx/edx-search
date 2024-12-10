.PHONY: clean quality requirements validate test test-with-es quality-python install-local

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

test.start_elasticsearch:
	docker compose up -d

test.stop_elasticsearch:
	docker compose stop

test_with_es: clean test.start_elasticsearch
	coverage run --source='.' manage.py test
	make test.stop_elasticsearch

compile-requirements: export CUSTOM_COMPILE_COMMAND=make upgrade
compile-requirements: ## Re-compile *.in requirements to *.txt (without upgrading)
	pip install -qr requirements/pip-tools.txt
	# Make sure to compile files after any other files they include!
	pip-compile --rebuild --allow-unsafe --rebuild -o requirements/pip.txt requirements/pip.in
	pip-compile --rebuild ${COMPILE_OPTS} -o requirements/pip-tools.txt requirements/pip-tools.in
	pip install -qr requirements/pip.txt
	pip install -qr requirements/pip-tools.txt
	pip-compile --rebuild ${COMPILE_OPTS} -o requirements/base.txt requirements/base.in
	pip-compile --rebuild ${COMPILE_OPTS} -o requirements/testing.txt requirements/testing.in
	pip-compile --rebuild ${COMPILE_OPTS} -o requirements/quality.txt requirements/quality.in
	pip-compile --rebuild ${COMPILE_OPTS} -o requirements/ci.txt requirements/ci.in
	pip-compile --rebuild ${COMPILE_OPTS} -o requirements/dev.txt requirements/dev.in
	# Let tox control the Django version for tests
	sed '/^[dD]jango==/d' requirements/testing.txt > requirements/testing.tmp
	mv requirements/testing.tmp requirements/testing.txt

upgrade: ## update the requirements/*.txt files with the latest packages satisfying requirements/*.in
	$(MAKE) compile-requirements COMPILE_OPTS="--upgrade"

test: test_with_es ## run tests and generate coverage report

install-local: ## installs your local edx-search into the LMS and CMS python virtualenvs
	docker exec -t edx.devstack.lms bash -c '. /edx/app/edxapp/venvs/edxapp/bin/activate && cd /edx/app/edxapp/edx-platform && pip uninstall -y edx-search && pip install -e /edx/src/edx-search && pip freeze | grep edx-search'
	docker exec -t edx.devstack.cms bash -c '. /edx/app/edxapp/venvs/edxapp/bin/activate && cd /edx/app/edxapp/edx-platform && pip uninstall -y edx-search && pip install -e /edx/src/edx-search && pip freeze | grep edx-search'
