// labb's design tokens. The source is tokens/*.json (Design Tokens Format 2025.10); this re-exports
// what scripts/build.ts generates from it, and the colour maths.
// Two layers of colour: reference colours (`palette`, `refColors`: every hue's ramp, no meaning)
// and system colours (`colors`: what each is for, pointing at palette steps per theme).
export * from "../generated/tokens";
export { contrast, rgb } from "./contrast";
