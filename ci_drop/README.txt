ci_drop/ - the encrypted mailbox of the cloud net test (tools/ci_drop.py, .github/workflows/net_cloud_guest.yml).

operator.pub          the public key the runner encrypts its results to (its private half is kept by whoever runs the test,
                      never in a repository)
<run>/runner.pub      the public key of one GitHub run; its private half exists only on that runner, for that run
<run>/invite.<n>.enc  an invite code (host address + game password) encrypted to that run's key
<run>/result.<n>.enc  that try's output folder (result, connection log, recording) encrypted to operator.pub
<run>/status.<n>.txt  that try's verdict in plain words, with the address and the password replaced by ***

Nothing here can be read without the matching private key; the status files hold no address and no password.
