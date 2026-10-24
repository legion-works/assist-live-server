# Architecture

This file gives the planned boundaries. The components are not built at this time.

## Server

The server package will contain gateways, clock conversion, storage, a turn detector, and provider adapters. The server repository owns the protocol schema.

## Client

The client package will give the Home Assistant integration its Python client. It will not contain model-runtime dependencies.

## Home Assistant app

The `ha-app/` directory will package the server for Home Assistant. It will not give the server an administrator credential.
