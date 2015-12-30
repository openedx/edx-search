clean:
	find . -name '*.pyc' -delete
	coverage erase
	rm -rf coverage htmlcov

quality:
	pep8 --config=.pep8 search
	pylint --rcfile=pylintrc search

requirements:
	pip install -r test_requirements.txt

validate: clean
	DJANGO_SETTINGS_MODULE=settings coverage run --source=search ./manage.py test
	coverage report
	make quality


.PHONY: clean, quality, requirements, validate
