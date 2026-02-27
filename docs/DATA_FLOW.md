# Data Flow Description

1. User selects file.
2. Client encrypts file locally.
3. Client encrypts filename.
4. Client splits file into chunks.
5. Client uploads encrypted chunks.
6. Server stores chunks in R2.
7. Client commits upload.
8. Server finalizes metadata.

At no point does plaintext file content reach the server.