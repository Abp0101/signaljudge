.PHONY: demo test check clean

PYTHON ?= python3

demo:
	PYTHONPATH=src $(PYTHON) -m signaljudge demo

test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests -v

check: test
	PYTHONPYCACHEPREFIX=/tmp/signaljudge-pycache PYTHONPATH=src $(PYTHON) -m compileall -q src tests

clean:
	$(PYTHON) -c "import shutil; [shutil.rmtree(p, ignore_errors=True) for p in ['artifacts', '.signaljudge']]"
