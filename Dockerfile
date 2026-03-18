# Build and run with docker_run.sh
# e.g., ./docker_run.sh -q example.com
#
# Thank you to Ilya Glotov (https://github.com/ilyaglow) for help with
# this minimal alpine image

FROM python:3-alpine

ADD requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt \
    && adduser -D gitgot

VOLUME ["/gitgot/logs", "/gitgot/states"]

WORKDIR /gitgot
USER gitgot

ADD checks /gitgot/checks
ADD gitgot.py .
ENTRYPOINT ["python3", "gitgot.py"]
CMD [ "-h" ]
