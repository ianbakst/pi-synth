# pi-audio-hat — Board BOM

This folder holds the **component-level** bill of materials for the `pi-audio-hat`
board — every part placed on the PCB (DAC IC, passives, transformer, connectors).

Do not hand-edit this; generate it from the KiCad schematic so it stays in sync:

- **CSV (for ordering):** KiCad → Schematic Editor → Tools → *Generate BOM* →
  export as `pi-audio-hat-bom.csv` in this folder.
- **Interactive HTML (for hand-assembly):** the [iBOM](https://github.com/openscopeproject/InteractiveHtmlBom)
  plugin → export `ibom.html` here.

The board itself is one line item in the project-level [BOM](../../../BOM.md).
