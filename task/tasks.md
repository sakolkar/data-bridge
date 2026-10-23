# Breakdown of the Spec into Small Tasks

Tasks are ordered by prerequisite. Complete all **Required** tasks through Phases 1 and 2; Phase 3 and Bonus tasks are optional. Each task should be implemented and verified before moving to its dependents.

## Phase 0 - Setup

### Task 0.1 - Initialize project structure (Required)

**Description**
- Establish `data-bridge/src/` as the FastAPI service source tree and `data-bridge/test/` as the pytest test tree.
- Add the minimal Python package, dependency manifest, and application entry point needed for the existing Dockerfile to build and run.

**Acceptance criteria**
- The source and test directories exist and are importable/discoverable by the chosen tools.
- Dependencies are declared in a reproducible manifest.
- The configured ASGI application starts locally and in the provided Docker setup.
- A minimal pytest test can be discovered and run.

### Task 0.2 - Establish configuration and application lifecycle (Required)

**Description**
- Read the required `DATABRIDGE_SECRET_KEY` and `DATABRIDGE_SQLITE_PATH` environment variables at application startup, without defaults.
- Validate a Fernet-compatible key and an absolute SQLite file path, check existing databases read-only for SQLite integrity, and prepare missing database parent directories.
- Keep configuration separate from route and connector implementation.

**Acceptance criteria**
- The service starts with documented environment configuration and exposes immutable settings through application lifespan state.
- Missing/blank values, invalid keys, relative database paths, directory targets, and storage preparation failures produce clear startup errors without revealing the key.
- The database location is configurable without changing code and is not restricted to a base directory.
- Configuration validation creates missing parent directories and checks existing databases read-only, without creating a missing database or altering existing contents.
- The connection store initializes its database/table separately during startup as part of task 1.3.
- Importing the application does not require configuration or perform filesystem writes.
- Encryption, database persistence, and connector file-root configuration remain separate tasks.

### Task 0.3 - Add a basic health endpoint (Required)

**Description**
- Expose a lightweight endpoint that confirms the HTTP service is running.

**Acceptance criteria**
- A request to the documented health endpoint returns a success status and a small JSON response.
- The endpoint does not depend on an existing connection or SFTP server.

## Phase 1 - Connection Management + Local Filesystem Connector

### Task 1.1 - Define connection and connector models (Required)

**Description**
- Define the connection configuration fields and the common connector contract for `list`, `read`, `write`, and bounded `head`.
- Keep connector operations independent of the HTTP layer.

**Acceptance criteria**
- Connection configuration represents at least a unique name, type, and type-specific settings.
- The connector contract specifies the inputs and outputs/errors for listing files, reading bytes, and writing bytes.
- The contract does not expose local-filesystem-only behavior.
- `head` returns initial bytes bounded by positive line and byte limits without reading the entire file; parsing and schema inference are separate tasks.

**Implementation scope**
- Generic models and the abstract connector contract are implemented without concrete backends.
- This increment also brings forward SQLModel-backed SQLite configuration CRUD and encrypted write-only settings from tasks 1.3, 1.4, and B.3.
- The singular `/connection` API accepts URL-encoded names in query parameters. Generic type strings/settings are accepted; type-specific validation and supported-type enforcement remain deferred.

### Task 1.2 - Implement local filesystem connector (Required)

**Description**
- Implement the connector contract for a configured local directory.
- Treat file contents as bytes so transfers can remain data-agnostic.

**Acceptance criteria**
- `list` returns the files available under the configured root.
- `read` returns the requested file's bytes.
- `write` creates or replaces the requested destination file with the supplied bytes.
- Paths cannot escape the configured root through traversal or absolute-path input.
- Missing files and filesystem failures produce useful connector-level errors.

### Task 1.3 - Persist connection configurations (Required)

**Description**
- Add a durable connection store using SQLite, JSON, or another simple persistent mechanism.
- Support creating and retrieving connections by name; include listing if needed by the API.

**Acceptance criteria**
- Stored connection data includes name, type, and type-specific settings.
- A connection remains available after the service process is restarted.
- Duplicate names and unsupported connection types are rejected explicitly.
- Secrets such as passwords are not returned in ordinary connection-list/detail responses.

**Current increment**
- Generic configuration persistence and full CRUD are implemented with SQLModel; settings are encrypted with the configured Fernet key.
- Unsupported-type rejection remains pending until concrete connectors and resolution are implemented.

### Task 1.4 - Add local connection creation and retrieval API (Required)

**Description**
- Add HTTP operations to create a local connection and retrieve/list saved connections.
- Validate request fields before persisting.

**Acceptance criteria**
- A valid local connection request is persisted and returns a useful success response.
- The API can retrieve/list the connection after creation.
- Missing fields, invalid names, and duplicate names return clear client errors rather than stack traces.

**Current increment**
- Generic create/list/retrieve/replace/delete routes are available at `/connection`, with `name` in the query string.
- Local-specific configuration validation remains pending; these routes do not yet instantiate or verify a local connector.

### Task 1.5 - Add connector resolution for saved connections (Required)

**Description**
- Create the shared mechanism that loads a saved connection and instantiates its connector.
- Keep connector selection out of individual file and transfer handlers where possible.

**Acceptance criteria**
- A saved local connection resolves to a working local connector.
- Unknown connection names return a distinguishable not-found error.
- Callers receive the common connector contract, not local-specific implementation details.

### Task 1.6 - Expose local file listing API (Required)

**Description**
- Add an HTTP endpoint to list files through a named connection.

**Acceptance criteria**
- Listing a local connection returns the files under its configured directory.
- An unknown connection returns an appropriate not-found response.
- Filesystem errors are mapped to a clear API error and do not expose a traceback.

### Task 1.7 - Add basic file preview / head API (Required)

**Description**
- Add an endpoint that reads and returns the first several rows or lines of a file through any resolved connector.
- At this stage, preview content; schema inference is a separate optional Phase 3 task.

**Acceptance criteria**
- The preview returns a bounded number of initial rows/lines and does not return the entire file.
- The endpoint works through the connector contract rather than directly accessing local paths.
- A missing file returns an appropriate not-found response.
- The response clearly identifies the file and preview content.

### Task 1.8 - Add Phase 1 integration tests (Required)

**Description**
- Exercise the local connection flow through the HTTP API and verify persistence and file operations.

**Acceptance criteria**
- Tests cover creating a local connection, listing files, and previewing a file.
- A persistence test proves a saved connection can be loaded by a newly initialized store/application.
- Tests cover at least a duplicate connection and a missing file.
- Tests use temporary directories/storage and do not depend on developer-specific paths.

## Phase 2 - SSH/SFTP Connector + Data Transfer

### Task 2.1 - Extend connection configuration for SFTP (Required)

**Description**
- Add validated SFTP settings: host, port, username, and password.
- Ensure secrets are handled separately from public connection representations.

**Acceptance criteria**
- SFTP configurations can be stored and loaded with all required settings.
- Invalid or missing host, port, username, or password is rejected with a useful message.
- API responses do not expose the stored password.

### Task 2.2 - Implement SFTP connector (Required)

**Description**
- Implement `list`, `read`, and `write` for SFTP using an established SSH/SFTP library.
- Match the same connector contract as the local implementation.

**Acceptance criteria**
- The connector can list, read, and write remote files using stored credentials.
- File data is handled as bytes.
- Authentication, connection, and remote-file errors are surfaced as useful connector errors.
- The implementation does not require changes to the common connector contract.

### Task 2.3 - Resolve SFTP connections through the common factory (Required)

**Description**
- Extend connector resolution to create SFTP connectors from saved connection settings.

**Acceptance criteria**
- An SFTP connection resolves through the same mechanism used for local connections.
- File listing through the API works for an available SFTP server.
- A stopped server or invalid credentials produce a clear error response, not an unhandled exception.

### Task 2.4 - Define transfer request and status model (Required)

**Description**
- Define transfer input and persistent/observable status fields before implementing the transfer endpoint.
- Include source and destination connection/file names, state, timestamps, and transferred byte count.

**Acceptance criteria**
- A transfer can be uniquely identified.
- Status can represent at least in-progress, completed, and failed outcomes.
- Status includes a start time, completion time when finished, and byte count when known.
- Failure status can include a caller-safe error description.

### Task 2.5 - Implement connector-agnostic transfer orchestration (Required)

**Description**
- Implement the core transfer operation by reading bytes from a source connector and writing them to a destination connector.
- Keep transport selection and connection-specific details in the connectors/factory.

**Acceptance criteria**
- The transfer service accepts resolved connectors and source/destination paths.
- It contains no connector-type conditional branching.
- It copies bytes without parsing or transforming the file content.
- It reports the number of bytes transferred.

### Task 2.6 - Persist and expose transfer status (Required)

**Description**
- Store transfer records so their outcome can be queried after the operation and service restart.

**Acceptance criteria**
- A transfer record is created before execution and updated on completion or failure.
- The status API returns the identifier, state, timestamps, and byte count.
- Failed transfers are recorded rather than disappearing or appearing successful.

### Task 2.7 - Add transfer creation API (Required)

**Description**
- Add an HTTP endpoint that validates a transfer request, resolves both connections, runs the transfer, and returns its status.

**Acceptance criteria**
- The endpoint supports local-to-SFTP and SFTP-to-local transfers.
- Invalid requests and unknown connections return clear client errors.
- Source-not-found and connection failures use appropriate HTTP status codes.
- The response includes the transfer identifier and observable status details.

### Task 2.8 - Add Phase 2 integration tests (Required)

**Description**
- Verify the common transfer pipeline across connector types and cover its failure paths.

**Acceptance criteria**
- Automated tests verify byte-for-byte transfers in both directions using a local SFTP test server or a test double.
- Tests verify completed status, timestamps, and byte count.
- Tests verify missing source files and failed connections are reported and recorded.
- The transfer orchestration tests do not depend on connector-specific branches.

## Phase 3 - Head Utility + Polish (Optional)

### Task 3.1 - Infer CSV schema in previews (Optional)

**Description**
- Extend preview responses for CSV files with headers and inferred column types.

**Acceptance criteria**
- The response includes column names and a type for each column.
- Types include string, integer, float, boolean, and date where reasonably inferable.
- Empty, malformed, or ambiguous values are handled predictably and documented.

### Task 3.2 - Infer JSON schema in previews (Optional)

**Description**
- Extend preview responses for JSON arrays of objects with field names and inferred types.

**Acceptance criteria**
- The response includes field names and inferred types for array-of-object input.
- Unsupported or malformed JSON produces a useful error response.
- Heterogeneous/missing values do not cause an unhandled exception.

### Task 3.3 - Verify schema preview works through every connector (Optional)

**Description**
- Ensure the schema preview reads through the common connector interface, including SFTP.

**Acceptance criteria**
- CSV and JSON preview behavior works for both local and SFTP connections.
- Parsing/schema logic does not access connector-specific paths or APIs.

### Task 3.4 - Polish API validation and error mapping (Optional)

**Description**
- Review connection creation, file operations, previewing, and transfers for consistent, actionable errors.

**Acceptance criteria**
- Invalid input returns a 4xx response with a useful message.
- Missing connections/files return appropriate not-found responses.
- SFTP failures explain the actionable cause where it is safe to do so.
- Unexpected internal failures do not return stack traces or sensitive settings.

## Submission and Documentation (Required)

### Task D.1 - Write project README (Required)

**Description**
- Document setup, dependencies, configuration, startup, API usage, design decisions, and remaining improvements.
- Include at least one API guide format: example `curl` requests, Postman collection, or OpenAPI documentation.

**Acceptance criteria**
- A new developer can follow the README to run the service and its tests.
- The README documents connection creation, file listing/preview, and transfers.
- Required environment settings and SFTP test setup are explained.
- Design tradeoffs and possible future improvements are stated.

### Task D.2 - Add AI collaboration summary (Required)

**Description**
- Add `ai-collaboration-summary.md` at the project root, following the prompt and limits in the specification.

**Acceptance criteria**
- The file describes tools/workflow, AI contributions, and engineer direction/corrections honestly.
- It gives 2-3 concrete prompt/output examples.
- It is no more than 500 words.

### Task D.3 - Verify submission and container workflow (Required)

**Description**
- Run the documented build, tests, and service startup path, including the provided Docker Compose setup where available.

**Acceptance criteria**
- The documented commands work from a clean checkout with declared dependencies.
- The service responds on the documented port.
- Phase 1 and Phase 2 required test coverage passes.
- Any limitations or unavailable external test services are documented accurately.

## Bonus (Optional)

### Task B.1 - Add connection health checks (Optional)

**Description**
- Provide an endpoint to verify that a saved connection can currently be used.

**Acceptance criteria**
- Health checking uses the connector abstraction and does not transfer or modify user files.
- The response distinguishes reachable connections from authentication/availability failures.

### Task B.2 - Stream large-file transfers (Optional)

**Description**
- Replace whole-file buffering with bounded-memory streaming where supported by the connectors.

**Acceptance criteria**
- Large files transfer without loading the entire file into memory.
- Byte counts and transfer failure status remain accurate.
- Existing small-file transfer behavior remains unchanged.

### Task B.3 - Encrypt stored credentials (Optional)

**Description**
- Add encryption at rest for SFTP credentials, with key management supplied outside the repository.

**Acceptance criteria**
- Credentials are encrypted in persistent storage and decrypted only when needed to connect.
- Encryption keys are not committed or returned by the API.
- Missing/invalid key configuration fails clearly rather than silently storing plaintext.

### Task B.4 - Add another connector type (Optional)

**Description**
- Demonstrate extensibility by implementing an additional connector (for example, S3) using the existing connector contract.

**Acceptance criteria**
- The connector supports list/read/write and can be resolved from stored configuration.
- Existing transfer orchestration requires no connector-type-specific modification.

### Task B.5 - Add connector contract tests (Optional)

**Description**
- Create shared tests that each connector implementation must pass.

**Acceptance criteria**
- The same contract test suite verifies list/read/write semantics for local and SFTP connectors.
- Test-specific setup is isolated from production connector behavior.

### Task B.6 - Serve interactive API documentation (Optional)

**Description**
- Expose generated OpenAPI/Swagger UI documentation from the running service.

**Acceptance criteria**
- The API documentation is reachable at a documented URL.
- It describes request and response schemas for the required operations.