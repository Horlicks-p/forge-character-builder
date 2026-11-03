"""Pure-Python core for the Forge Character Builder extension.

Lives at the extension root (not under ``scripts/``): Forge Neo adds each
extension root to ``sys.path``, so ``scripts/character_builder.py`` can import
this package directly. Do not import Gradio or Forge modules from here, so the
package stays testable without a WebUI.
"""
