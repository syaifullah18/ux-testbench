# 002: Scratch copy validation for config edits

**Context**  
Researchers use the web-based Studio to edit project configuration (YAML files). Misconfigured files can cause the application to crash or behave incorrectly for active participants.

**Decision**  
Every change made in the Studio is validated against a temporary scratch copy of the whole project. Only if the project loads successfully without errors are the changes committed to the live project files. 

**Alternatives rejected**  
- **Immediate write with try/catch**: Leaves the live project in a broken state if the write introduces a syntax error, potentially disrupting live studies.
- **Client-side only validation**: Difficult to perfectly replicate all server-side logic in JS, leading to edge cases where bad configs still make it through.

**Costs accepted**  
- Increased disk IO and CPU overhead on every edit in the Studio, since we are doing full copies of the project folder (minus history) to a temp directory.
- Slower save times. This is acceptable since configuration edits are infrequent compared to participant interactions.
