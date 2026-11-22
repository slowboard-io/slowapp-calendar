# The calendar slowapp. Standard library and Pillow only, so it builds on the slim base, pinned by
# tag and digest; Dependabot opens one PR when slowapp-base releases.
FROM ghcr.io/mike-anderson/slowapp-python:1.5.0@sha256:ba854f3f6bda285b05ebdfb73115475eeef545c87b1d9c47a1d7434de66777a6
COPY slowapp.toml /app/slowapp.toml
COPY calapp /opt/app/calapp
COPY ui/page.html ui/bridge.js tools/build_ui.py /opt/ui-src/
# The page (contract 4 [ui] entry): one self-contained file, the bridge inlined.
RUN mkdir -p /ui && python3 /opt/ui-src/build_ui.py /opt/ui-src/page.html /opt/ui-src/bridge.js /ui/index.html
ENV PYTHONPATH=/opt/app:/opt/slowapp
RUN python3 -m compileall -q /opt/app && python3 -c "import calapp.sync, calapp.platform"
ENTRYPOINT ["python3", "-m", "calapp"]
CMD ["job", "render"]
