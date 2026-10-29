# Architecture

This file gives the package boundaries. The project is experimental and has no release.

## Server

The server package contains gateways, clock conversion, storage, a turn detector, and provider adapters. The server repository owns the protocol schema.

## Client

The client package gives the Home Assistant integration its Python client. It has no model-runtime dependencies.

## Home Assistant app

The `assist-live-server/` directory packages the server for Home Assistant. It does not give the server an administrator credential.
