# splan/1: a recipe for a superpermutation

A recipe is a small file that a fixed decoder expands into the word, as a stream. The word of n = 14 has 94 GB; the
recipe is about 0.6 MB. The idea of publishing a construction as a recipe instead of the word is William Echols's
(`superperm-2lift/1`, n = 14, 2026-10-17); this format generalises it to "seed + generator + plan + footer".

The decoder never runs anything that comes with the recipe. A recipe names a generator by id; the id selects a program
from a fixed list in `checker/registry.py`, built from C sources that ship with the bot. The recipe supplies data: a seed, a
plan, the alphabet.

## Container

One of

* `NAME.splan.json.xz`: an xz-compressed JSON object (the usual form);
* `NAME.splan.json`: the same, uncompressed;
* a directory `NAME.splan/` holding `recipe.json` and the files it names (`seed.txt.xz`, `plan.uplan`, ...). Only a
  directory may refer to files, and only to files inside it (no absolute paths, no `..`, no drive letters).

A decompressed JSON larger than 64 MiB, a seed larger than 16 MiB or a plan larger than 64 MiB is refused.
Names of the form `superpermutation-N-L.splan.json.xz` are compared by the GitHub Action with the n and the length
found.

## Fields

| field | type | meaning |
|---|---|---|
| `format` | string | exactly `"splan/1"` |
| `n` | integer | number of symbols, 2 to 14 |
| `alphabet` | string | n distinct printable ASCII characters (no space); the symbols of the word, as written |
| `length` | integer | number of letters of the word (the footer: checked against what the decoder writes) |
| `word_sha256` | string or null | SHA-256 (lowercase hex) of the word as written: the letters only, no line end. null: not stated |
| `generator` | object | `{"id", "version", "source_sha256"}`, see below |
| `seed` | object | `{"text": "...", "sha256": H}` or `{"file": "seed.txt.xz", "sha256": H}` |
| `plan` | object | same form as `seed`; required or forbidden depending on the generator |
| `credits`, `provenance`, `source`, ... | any | ignored by the decoder |

`sha256` of `seed` and `plan` is that of the exact bytes: UTF-8 of `text`, or the decompressed content of `file` (a file
ending in `.xz` is decompressed). A mismatch refuses the recipe before anything runs.

The hash `word_sha256` depends on the alphabet: the same word under another labelling has another hash. It is the hash of
the letters as the decoder writes them with the stated `alphabet`.

## Generators

`generator.source_sha256` must equal a hash that the bot accepts for that generator version (SHA-256 of the C
source). A recipe made for different code is refused; it does not run "its" code. Both ids below are `gen14.c`
(`gens/gen14.c`, the SHA-256 is in `checker/registry.py`).

### `gen14-uplan` version 1

n = 14. `seed` = a selection on 11 symbols (the text of `arrange/data/n12t-selection.txt.xz` of
jhalfar/superpermutations: one line "ordering F|S|D" per 2-loop, `#` comments). `plan` = `N14-UNITPLAN 1`: one line per
closed trail unit (`D d g j` or `W w i0 way`; the order and the openings of the units), with an optional line
`# expect length L sha256 H` that the generator checks at the end. Command: `gen14 plan SEL PLAN --alpha ALPHABET`.
Details: https://github.com/jhalfar/superpermutations. The construction (slices, transport, completion, closed trails) is
Jay Pantone's; the 2-lift as a recipe is William Echols's; the selection and the unit plan are Jakub Halfar's.

### `pantone-2lift` version 1

n = 14. `seed` as above; no `plan`. The word is the 2-lift of the selection in Echols's order. Command:
`gen14 echols SEL --alpha ALPHABET`. This is what a `superperm-2lift/1` file means: `tools/make_recipe.py import-echols`
reads his JSON as data (format, alphabet, length, seed and `seed_sha256` are checked) and writes a `splan/1` file for
this generator with his seed and alphabet `123456789ABCDE`. His file has no hash of the word, so `word_sha256` is
null unless the importer is given one (`--word-sha256`, then `provenance` says it was added at import time).
Nothing of his Python is run.

New ids are added to `checker/registry.py` together with their source; a recipe for an unknown id is refused.

## Decoding and checking

1. read the container, check `format`, `n`, `alphabet`, `length`, `word_sha256` syntax, generator id, version and
   source hash, and that the generator makes words for this n;
2. check the SHA-256 of `seed` and `plan`;
3. write them into a fresh private temporary directory and start the registry's binary with fixed arguments (no shell),
   stdin closed, output a pipe (`checker/decode.py`);
4. `checker/check_any.py` reads the pipe, computes the SHA-256 of the letters on the fly and feeds the checker (`vfast` /
   `verify_stream`) with the stated `alphabet`;
5. verdict `ok` = every one of the n! permutations occurs, no byte outside the alphabet, the number of letters equals
   `length`, and the SHA-256 equals `word_sha256` if one is stated.

Nothing is written to disk but the seed and the plan (about 6 MB). Memory: the generator 0.6 GB, the checker n!/8 bytes
(10.9 GB at n = 14).

## Plain words

`NAME.txt` and `NAME.txt.xz` stay supported: n distinct symbols, found from the first 16 MB of the word, trailing line
ends ignored; the same checker, the same hash of the letters. No limit except 2 * 10^11 letters.

## Making a recipe

```
python -I tools/make_recipe.py uplan SEL.txt.xz PLAN.uplan.xz OUT.splan.json.xz     # length and hash from the plan's "# expect" line
python -I tools/make_recipe.py import-echols 93904936032.spc.xz OUT.splan.json.xz [--word-sha256 H]
```

## Stability

`splan/1` is fixed as of v1.0: the fields above keep their meaning, and a later change gets a new name (`splan/2`)
while the bot keeps reading `splan/1` files. A new generator is added as a new id in `checker/registry.py`; the ids
that exist keep their accepted source hashes.

## Not covered

* recipes for n <= 13: the words are small enough to send as they are (`.spra.xz` is about 1.3 MB at n = 13, `.txt.xz`
  4 MB at n = 12), so there is no generator for them;
* random access (`--skip`) and a separate plan footer (hash of seed, plan and generator together).
