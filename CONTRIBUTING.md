# Contributing

Bug reports and patches are welcome.

## Before you open a pull request

Run the suite. It needs GTK4 but not a compositor, and it will generate its
own epub if you do not point it at a library:

```sh
./tests/run.sh                 # or: ./tests/run.sh ~/Books/some-book.epub
```

Every suite must end in `FAILURES: none`.

## What a change should come with

- A test that fails before it and passes after, in the suite it belongs to:
  `test_shelf.py` for layout and hit-testing, `test_interaction.py` for
  what the mouse does, `test_wiring.py` for how the widgets are put
  together, `test_cards.py` for the floating cards, `test_reader.py` for
  paging and typography.
- Comments that say *why*, where the reason is not obvious from the code.
  The existing ones are the house style: they explain the failure the code
  is avoiding, not what the next line does.

## Style

`almari.py` is one file on purpose — it is a single program with no import
graph worth having. Keep functions small and named for what they answer.
Four-space indent, 79 columns.

## Licence

By contributing you agree that your contributions are licensed under the
Apache License 2.0, the same as the rest of the project.
