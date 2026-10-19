# The calendar slowapp. Standard library and Pillow only, so it builds on the slim base, pinned by
# tag and digest; Dependabot opens one PR when slowapp-base releases.
# TODO(contract 6): the manifest is contract 6, which needs slowapp-base 1.5.0 (token(), artifacts).
# Until 1.5.0 is released this pin is 1.4.1, whose kit refuses SLOWAPP_CONTRACT=6: bump this line
# and app.yml's workflow ref to v1.5.0 together before building the image.
FROM ghcr.io/mike-anderson/slowapp-python:1.4.1@sha256:eb97f920f1bce8efb7a3eb4827cbafff53672f50102d730ee955ae8f7abcde1b
COPY slowapp.toml /app/slowapp.toml
COPY calapp /opt/app/calapp
ENV PYTHONPATH=/opt/app:/opt/slowapp
RUN python3 -m compileall -q /opt/app && python3 -c "import calapp.sync, calapp.platform"
ENTRYPOINT ["python3", "-m", "calapp"]
CMD ["job", "render"]
