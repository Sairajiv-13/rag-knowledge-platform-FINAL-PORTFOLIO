# Streaming answers

Answers can be streamed to the client using server-sent events. The stream
first emits the citations that ground the answer, then emits the answer text
in incremental deltas as the language model generates it, and finally emits a
completion event carrying token usage.

Streaming lets a client show the first words of an answer within a second even
when the full answer takes several seconds to generate. The citations arrive
first so the client can render sources before the prose that references them.
