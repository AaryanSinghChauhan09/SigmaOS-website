.PHONY: build test install clean
build:
	python3 -m compileall -q src
test:
	python3 -m unittest discover -s tests -v
install:
	install -Dm755 bin/sigma-website $(DESTDIR)/usr/bin/sigma-website
clean:
	rm -rf src/__pycache__ tests/__pycache__
