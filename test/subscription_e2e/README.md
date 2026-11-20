# Checkpoint Pro end-to-end checks

These checks verify the test-only $19.99/month subscription against the
Firebase Emulator Suite and the real Checkpoint app. They never contact the
real Firebase project, and they **reset the emulators' data** before running.

## One-time setup

```powershell
npm install -g firebase-tools        # current versions need Java 21 for the Firestore emulator
cd functions; npm install; cd ..
```

With Java 11, use firebase-tools 13 through npx instead of a global install:
`npx -y firebase-tools@13 emulators:start --only auth,firestore,functions`.

`functions/.env` sets `CHECKPOINT_TEST_PAYMENTS=false`. Without it, the CLI
stops at an interactive prompt and the Functions emulator never loads.

## Run with test mode on

```powershell
Set-Content -Encoding utf8 functions\.env.local "CHECKPOINT_TEST_PAYMENTS=true"
firebase emulators:start --only auth,firestore,functions      # terminal 1
npm run test:subscription-e2e                                 # terminal 2
```

This runs the backend and Firestore rules checks, then launches the app twice
with the same user-data folder:

- **purchase**: register a new account, which starts free. Then check that:
  - paid pages, buttons and IPC/backend calls are refused, even with a tampered client flag;
  - free pages still work;
  - dummy checkout (double-clicked) unlocks both feature groups.
- **restart**:
  - the session is restored and access is still Pro;
  - signing out and in as an existing account without subscription data shows it as free;
  - switching back restores Pro;
  - expiry locks both feature groups.

## Run with test mode off

Stop the emulators, delete `functions\.env.local`, start them again, then run
`npm run test:subscription-e2e`. The server must reject checkout, and the app
must hide the checkout and refuse a direct call.

## Dummy card checks

Each run types a random dummy card number at checkout. It then checks that
the number appears in none of these:

- the recorded main-process and page requests;
- console lines;
- app stdout/stderr;
- any file under the app's user-data folder (Local Storage, IndexedDB, caches, session, profiles);
- the Firestore data;
- the emulator logs.

Results go to `test/subscription_e2e/runs/<time>/report.json` (git-ignored).
