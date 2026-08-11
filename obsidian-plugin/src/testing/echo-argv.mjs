// Test fixture: print the arguments this process actually received, so a test can prove the
// bridge passed them as an array rather than through a shell.
process.stdout.write(JSON.stringify(process.argv.slice(2)));
