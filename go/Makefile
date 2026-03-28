BINARY   := xray-monitor
MODULE   := github.com/graynextry/xray-monitor
LDFLAGS  := -s -w
CMD      := ./cmd/xray-monitor

# Build for the current OS/arch
.PHONY: build
build:
	go build -ldflags="$(LDFLAGS)" -o $(BINARY) $(CMD)

# Build for Linux amd64 (typical VPS)
.PHONY: linux-amd64
linux-amd64:
	GOOS=linux GOARCH=amd64 go build -ldflags="$(LDFLAGS)" -o $(BINARY)-linux-amd64 $(CMD)

# Build for Linux arm64 (ARM VPS / Raspberry Pi 4)
.PHONY: linux-arm64
linux-arm64:
	GOOS=linux GOARCH=arm64 go build -ldflags="$(LDFLAGS)" -o $(BINARY)-linux-arm64 $(CMD)

# Build all targets
.PHONY: all
all: linux-amd64 linux-arm64

# Install dependencies
.PHONY: deps
deps:
	go mod tidy

# Run locally (for development)
.PHONY: run
run:
	go run $(CMD) --server 127.0.0.1:10085

# Lint
.PHONY: vet
vet:
	go vet ./...

.PHONY: clean
clean:
	rm -f $(BINARY) $(BINARY)-linux-*
