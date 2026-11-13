// The tokens as CSS variables: --lb-ref-* (the palette), --lb-sys-color-* (system colours, light by
// default, dark when the system asks unless data-theme="light", and whenever data-theme="dark"),
// and the rest (--lb-space-*, --lb-radius-*, …). Also published as @labb/tokens/tokens.css.
import { cssText } from "../generated/tokens";

export function tokensCss() {
  return cssText;
}
