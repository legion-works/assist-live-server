ARG BUILD_FROM=assist-live-server:check
FROM ${BUILD_FROM}
USER root
COPY run.py /app/run.py
LABEL io.hass.type="addon" io.hass.name="assist-live-server" \
      io.hass.description="Experimental realtime voice server" \
      io.hass.url="https://github.com/legion-works/assist-live-server"
ENTRYPOINT ["python", "/app/run.py"]
CMD ["assist-live-server"]
