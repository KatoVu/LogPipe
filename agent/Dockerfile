# Agent image: the static binary on debian-slim for journalctl (~155 MB unpacked; distroless had no journalctl).
FROM --platform=$BUILDPLATFORM golang:1.23-alpine AS build
ARG TARGETOS TARGETARCH TARGETVARIANT VERSION=dev
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH GOARM=${TARGETVARIANT#v} go build -trimpath -ldflags "-s -w -X main.version=$VERSION" -o /logpipe-agent .

# trixie's journalctl (systemd 257) reads journals written by current host systemd versions.
FROM debian:13-slim
RUN apt-get update \
	&& apt-get install -y --no-install-recommends systemd ca-certificates \
	&& rm -rf /var/lib/apt/lists/*
COPY --from=build /logpipe-agent /usr/local/bin/logpipe-agent
COPY docker/agent.yaml /etc/logpipe-agent/agent.yaml
ENV LOGPIPE_AGENT_CONFIG=/etc/logpipe-agent
VOLUME /var/lib/logpipe-agent
ENTRYPOINT ["/usr/local/bin/logpipe-agent"]
CMD ["run"]
