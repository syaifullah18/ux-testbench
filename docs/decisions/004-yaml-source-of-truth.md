# 004: YAML as the source of truth

**Context**  
Projects need a configuration format to define modules, questions, routing rules, and general settings. Researchers need to be able to edit these settings both via the web Studio and directly on the file system (e.g., version control).

**Decision**  
YAML files are used as the primary source of truth. The web-based Studio edits these exact same YAML files, rather than storing configuration in a database.

**Alternatives rejected**  
- **Database-driven configuration**: Would require a complex bespoke UI for every single setting and make it impossible for users to configure studies using standard IDEs and git workflows.
- **JSON**: Less readable for humans, doesn't support multiline strings easily (important for task prompts and descriptions), and lacks comments.

**Costs accepted**  
- The Studio requires a text editor interface (or form-to-YAML bridges) which can be intimidating for non-technical users.
- Potential syntax errors if edited directly via the file system (though `testbench check` mitigates this).
