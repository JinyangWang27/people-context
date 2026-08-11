// Test fixture: fail the way the CLI does, with a message on stderr and a non-zero exit.
process.stderr.write("database is locked");
process.exit(3);
