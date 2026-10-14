# Orbit — design research and redesign

Reviewed 15 public homepages on 14 October 2026, including their rendered desktop layouts. This is a qualitative design review, not a usability study or a claim that every site follows every pattern. Website campaigns and layouts change over time.

## Reference observations

| Reference | Observed design choice | Translation into Orbit |
|---|---|---|
| [Make](https://www.make.com/) | Clear automation proposition, visual workflow/product presentation, grouped capabilities | Connected operating-field diagram and separate tool settings |
| [Apple](https://www.apple.com/) | Large product focus, concise headlines, generous space, clear primary and secondary actions | One dominant headline, generous spacing, one launch action |
| [Samsung](https://www.samsung.com/us/) | Wide product imagery with compact navigation | Wide opening composition and compact horizontal navigation |
| [OPPO](https://www.oppo.com/) | Quiet neutral backgrounds, large product on the right, simple dark calls to action | Split editorial/product layout and restrained controls |
| [Outskill](https://outskill.com/) | Dramatic headline, strong contrast, a distinctive accent, thin structural lines | Confident headline, orange action accent, visible layout rules |
| [Linear](https://linear.app/) | Restrained interface, large typography, real product UI as evidence | Clear run inspector, restrained panels, actual persisted activity |
| [Stripe](https://stripe.com/) | Strong typographic hierarchy, broad composition, organized navigation | Deliberate scale contrast and consistent page structure |
| [Vercel](https://vercel.com/) | Simple symbolic centerpiece, spare layout, clear actions | Original Orbit centerpiece and reduced visual clutter |
| [Notion](https://www.notion.com/) | Bold readable headline, friendly voice, recognizable product preview | Plain-language copy and approachable task starters |
| [Framer](https://www.framer.com/) | Product demonstration as a major visual element; compact top navigation | Interactive workspace visualization rather than a stock illustration |
| [Figma](https://www.figma.com/) | Asymmetric typography and layered creative examples | Asymmetric hero balanced by a custom diagram |
| [Raycast](https://www.raycast.com/) | Focused promise, restrained navigation, strongly prioritized action | Task launch made prominent; secondary operations kept quieter |
| [Nothing](https://nothing.tech/) | Distinctive technical typography, strong brand character, full-bleed imagery | Monospaced instrument labels and a consistent identity throughout |
| [Arc](https://www.arc.net/) | Warm background, playful identity, large product preview | Warm paper palette and a more human tone |
| [n8n](https://n8n.io/) | Visible emphasis on agent workflows and user control; strong action accent | Approval is a first-class destination in the workflow diagram |

## Shared patterns applied

1. **Hierarchy before decoration.** One headline and one main action lead the page. Secondary actions use less visual weight.
2. **Give the product a visual identity.** Orbit uses an original orbital SVG, warm paper, charcoal, and orange. No reference logos, imagery, or proprietary artwork were copied.
3. **Use space and typography deliberately.** Larger headings, thin dividing rules, a consistent grid, and fewer boxed metric cards.
4. **Show the product itself.** The operating field uses real enabled-agent, active-task, and completed-run counts. Each node opens its relevant workspace page.
5. **Keep navigation predictable.** Overview, Runs, Agents, Tools, and Approvals stay available in one horizontal navigation bar. Mobile retains text labels.
6. **Progressively reveal detail.** The overview establishes the work; Activity, Tools, and Result tabs provide details for a selected run.
7. **Make actions understandable.** Research, planning, and writing starters prefill editable task prompts; they never submit automatically.

## Adaptation boundaries

These references are mainly marketing homepages. Orbit remains an operational application: live task history, approvals, tool arguments, error states, and connection status take priority over marketing sections. No invented customer logos, testimonials, success metrics, or capabilities were added. Motion is limited to subtle hover feedback and respects reduced-motion preferences. Fonts and the original diagram are local; no third-party font or image request was introduced.

## Implementation and verification

- Updated the React interface and responsive CSS; existing backend APIs and approval gates retained.
- Production TypeScript/Vite build passed.
- Browser checks cover task starter prefill, task submission, saved results, navigation, tool settings, and mobile layout.
