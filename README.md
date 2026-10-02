# Private agent status feed

Data-only branch for the user's task dashboard. Do not merge this branch into application or release branches.

Only sanitized task status and real observation timestamps belong here. No credentials, transcripts, private notes, filesystem paths or application code. The target observation interval is 10 minutes; delays and unknown periods must remain visible. A recent fetch does not make an old observation fresh.

This tree deliberately contains no workflow files. Server consumers must validate the JSON, preserve a last-good snapshot on failure, and serve it behind existing authentication.
