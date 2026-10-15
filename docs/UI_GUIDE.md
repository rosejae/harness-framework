# UI Design Guide

## Design principles
1. {principle 1 — e.g. "It should look like a tool. A dashboard used every day, not a marketing page."}
2. {principle 2}
3. {principle 3}

## AI-slop anti-patterns — don't
| Banned | Why |
|-----------|------|
| backdrop-filter: blur() | glass morphism is the most common tell of an AI template |
| gradient-text (text with a gradient background) | the #1 trait of an AI-built SaaS landing page |
| "Powered by AI" badge | decoration, not a feature. No value to the user |
| box-shadow glow animation | neon glow = AI slop |
| purple/indigo brand colors | the "AI = purple" cliché |
| the same rounded-2xl on every card | uniform rounded corners feel like a template |
| background gradient orb (blur-3xl circle) | decoration found on every AI landing page |

## Colors
### Background
| Use | Value |
|------|------|
| Page | {e.g. #0a0a0a} |
| Card | {e.g. #141414} |

### Text
| Use | Value |
|------|------|
| Primary text | {e.g. text-white} |
| Body | {e.g. text-neutral-300} |
| Secondary | {e.g. text-neutral-400} |
| Disabled | {e.g. text-neutral-500} |

### Data/semantic colors
| Use | Value |
|------|------|
| {positive/success} | {e.g. #22c55e} |
| {negative/error} | {e.g. #ef4444} |
| {neutral/default} | {e.g. #525252} |

## Components
### Card
```
{e.g. rounded-lg bg-[#141414] border border-neutral-800 p-6}
```

### Button
```
Primary: {e.g. rounded-lg bg-white text-black hover:bg-neutral-200}
Text:    {e.g. text-neutral-500 hover:text-neutral-300}
```

### Input field
```
{e.g. rounded-lg bg-neutral-900 border border-neutral-800 px-4 py-3}
```

## Layout
- Full width: {e.g. max-w-5xl}
- Alignment: {e.g. left-aligned by default. No center alignment}
- Spacing: {e.g. gap-3~4, space-y-8 between sections}

## Typography
| Use | Style |
|------|--------|
| Page title | {e.g. text-4xl font-semibold text-white} |
| Card title | {e.g. text-sm font-medium text-neutral-400} |
| Body | {e.g. text-sm text-neutral-300 leading-relaxed} |

## Animation
- {list only the animations allowed. e.g. fade-in (0.4s), slide-up (0.5s)}
- {every other animation is banned}

## Icons
- {e.g. inline SVG, strokeWidth 1.5}
- {e.g. do not wrap icons in an icon container (a rounded background box)}
