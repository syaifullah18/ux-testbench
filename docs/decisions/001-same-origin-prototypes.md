# 001: Same-origin prototype serving

**Context**  
Participants need to perform tasks on uploaded static HTML prototypes. We need to collect behavioral metrics like clicks, time, first click, scroll reversals, and viewport width.

**Decision**  
We accept the risk of serving prototypes on the same origin as the application, instead of a sandbox domain, to allow the parent task-runner frame to inject instrumentation via postMessage and measure interactions. 

**Alternatives rejected**  
- **Iframe with a different origin**: Blocks access to the prototype's DOM due to Cross-Origin Resource Sharing (CORS) rules. Without access to the prototype's DOM, we can't reliably detect first click coordinates or specific element clicks without forcing researchers to manually instrument every prototype HTML file.

**Costs accepted**  
- Because the prototypes run on the same origin, their JavaScript runs with the app's permissions. This requires that we heavily restrict who has access to the Studio (superadmin and project admin only). We trust the people deploying these prototypes not to insert malicious scripts.
