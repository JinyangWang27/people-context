// Test fixture: write far more than any output cap on the requested stream, then hang, so the
// bridge has to stop the process itself rather than waiting for it to finish.
const stream = process.argv[2] === "stderr" ? process.stderr : process.stdout;
stream.write("x".repeat(200000));
setInterval(() => {}, 1000);
