# gRPC Ingester

Asynchronous ingestion service for clone implant memory streams.

The protobuf contract is defined in `shared/protos/memory_stream.proto`. The
initial handler counts frames and bytes so the streaming boundary is explicit in
the project scaffold.
