1. test names in tests-to-implement must be categorized into the following categories based on whether they are safe to infer test oracles from or unsafe. safety criteria will gradually harden during different stages of development. safety criteria are not to be trusted as absolute, but to remain practical for their development cycle.

2. test names that are too broad to be implemented in a single test should not be implemented, instead the test name will be turned into a test category which will have its own list of tests-to-implement.

3. reusable oracles must be implemented separately in a separate module in a separate directory from e2e names reusable-oracles.

4. non-reusable oracles must be implemented directly inside the test function.

5. it is preferred that higher-level oracles are implemented by composition of lower-level oracles as long as they follow declarative design philosophy and each oracle has a clear name which is clear enough for understanding what the oracle specifies without the need for explanation or documentation.

6. the e2e and reusable-oracles modules must NOT own infra implementations.

7. tests must be explicitly implemented in the repo. runtime test generators are prohibited.

8. reusable-oracles dependency graph must be documented using visual diagrams in .md files.

9. tests must be categorized into the following categories based on their passing criteria: context-specific, context independent.

10. context-specific tests will only run in their own simulated scenario.

11. context-independent tests will run in every simulated scenario.

12. all simulated scenarios will be implemented will be implemented in simulated-scenarios directory inside infra directory and NEVER in e2e or reusable-oracles directories.