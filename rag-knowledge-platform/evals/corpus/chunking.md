# Chunking strategy

Documents are split into sentence-packed chunks of roughly four hundred
estimated tokens with a sixty token trailing overlap, so a chunk boundary
never lands mid-thought. Oversized paragraphs are split by sentence, and
pathological unpunctuated runs are hard-split by words.

Each chunk carries provenance metadata: the heading path for markdown and
HTML, or the page range for PDFs. Citations render this as a section path or
page numbers next to the source filename.
