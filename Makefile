clean:
	find . -name '*.pyc' -delete
	coverage erase
	rm -rf coverage htmlcov

quality:
	tox -e quality

requirements:
	pip install -r test_requirements.txt

validate: clean
	tox


.PHONY: clean, quality, requirements, validate
