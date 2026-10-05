# Security policy

math-rigor proves or refutes a mathematical statement locally, and reports
`proven` / `refuted` / `inconclusive` without conflating them. The sections below
state exactly what it may touch, so a reader can check the claim against the code
instead of taking it on trust.

## Reporting a vulnerability

Open a private report through this repository's **Security → Report a
vulnerability** tab. Please include the version (`package.json`), the input that
triggers it, and the smallest reproduction you can manage.

## What the plugin can do

| Surface | Behaviour |
|---|---|
| Processes | Spawns Python: a short probe to find an interpreter, and the proving server over stdio. Nothing goes through a shell, so no value is ever parsed as a command. |
| The server's environment | Exactly **one** variable: `MATH_RIGOR_HOME`. The harness's environment also holds model API keys; the server never sees them. |
| Files written | Everything lands in the plugin's own home, `$DSH_HOME/math-rigor`: the venv, a marker file, and the server's state. Nothing is written into the profile or the working directory. |
| Files read | `$DSH_HOME` (from the environment) and the mathematical input handed to a tool. |
| Network | One download, on first use: `<venv>/python -m pip install -r requirements.txt`. Nothing else reaches the network, and proving itself is entirely local. |
| Credentials | None. No `.credentials.yaml`, no `settings.yaml`, no `*_API_KEY` / `*_TOKEN`. |
| Dynamic code | None, in JavaScript or in Python — see below for why that matters here. |

## Why "no dynamic code" is the security property of a prover

A tool that accepts mathematical expressions is one `eval` away from executing
whatever it is handed. This one never takes that step:

* the input is lexed and parsed into an AST by `server/rigor/ast_nodes.py` — its
  own grammar, no `eval`, no `exec`, no `compile` of anything derived from input;
* a parsed statement is translated to SMT-LIB for `z3` (`server/rigor/smt.py`) or
  to `sympy` expressions built with library constructors, never by evaluating a
  string in a namespace;
* `tests/test_security.py` fails if an `eval(`, an `exec(` on a concatenated
  string, or a `shell=True` ever appears in the shipped Python.

So the worst case for a hostile expression is a parse error or a solver that
answers `inconclusive`, which is the same answer it gives for a hard theorem.

## The one download, and what it brings

`requirements.txt` pins every package to an exact version, and that is what makes
the download auditable before it happens:

```
mcp==2.2.0
sympy==1.14.0
z3-solver==5.1.0.0
mpmath==1.3.0
```

The install runs once, into `$DSH_HOME/math-rigor/venv`, and only when the file
changes: the plugin records the SHA-256 of `requirements.txt` in a marker beside
the venv and skips the install while the hash matches. `setupEnabled` turns the
install off entirely for a deployment that prepares the venv itself.

pip uses the machine's own index and credentials configuration, exactly as it
would from a terminal; this plugin adds no index of its own.

## Verifying this yourself

```sh
python tests/test_security.py     # 22 checks over the shipped files
python tests/run_all.py           # the security checks plus every other module
```

Each check reads the plugin's own files and names the file and line it objected
to, so the posture cannot drift away from the code without a failure.

## Scope and limits

* The pinned Python packages (`mcp`, `sympy`, `z3-solver`, `mpmath`) and the
  Harness itself are out of scope; this policy covers this plugin's code.
* The plugin runs the interpreter it is configured with or finds first; a
  compromised interpreter on `PATH` is the deployment's trust boundary, not a
  bypass of this plugin.
* Proving is local and offline, but it is not a proof *assistant*: an
  `inconclusive` answer means the statement was neither proven nor refuted, and
  the plugin reports it as such rather than guessing.
