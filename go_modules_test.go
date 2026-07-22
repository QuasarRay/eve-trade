package evetrade_test

import (
	"bytes"
	"os"
	"path/filepath"
	"regexp"
	"testing"
)

func TestGoBackendUsesSingleRootModule(t *testing.T) {
	removedModules := []string{
		filepath.Join("distributed-backend", "proto"),
		filepath.Join("distributed-backend", "src", "api"+"-"+"gateway"),
		filepath.Join("distributed-backend", "src", "market"),
		filepath.Join("distributed-backend", "src", "messaging"),
		filepath.Join("distributed-backend", "src", "observability"),
		filepath.Join("distributed-backend", "src", "settlement"+"-"+"worker"),
	}

	root, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}

	for _, module := range removedModules {
		t.Run(module, func(t *testing.T) {
			if _, err := os.Stat(filepath.Join(root, module, "go.mod")); !os.IsNotExist(err) {
				t.Fatalf("%s/go.mod still exists; Go backend should use the root module", module)
			}
		})
	}
}

func TestKubernetesMigrationCopiesMatchSource(t *testing.T) {
	pairs := [][2]string{
		{
			"distributed-backend/src/trade-settlement/migrations/0001_settlement_schema.sql",
			"distributed-backend/orchestration/kubernetes/base/migrations/0001_settlement_schema.sql",
		},
		{
			"distributed-backend/src/trade-settlement/migrations/0002_merge_item_stack_constraints.sql",
			"distributed-backend/orchestration/kubernetes/base/migrations/0002_merge_item_stack_constraints.sql",
		},
		{
			"distributed-backend/src/trade-settlement/migrations/0003_settlement_hardening_and_outbox.sql",
			"distributed-backend/orchestration/kubernetes/base/migrations/0003_settlement_hardening_and_outbox.sql",
		},
	}

	for _, pair := range pairs {
		source, err := os.ReadFile(filepath.FromSlash(pair[0]))
		if err != nil {
			t.Fatal(err)
		}
		copy, err := os.ReadFile(filepath.FromSlash(pair[1]))
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(source, copy) {
			t.Fatalf("%s does not match %s", pair[1], pair[0])
		}
	}
}

func TestProductionOverlayTemplatesDigestsAndDeployRequiresPublishedDigests(t *testing.T) {
	manifest, err := os.ReadFile(filepath.FromSlash("distributed-backend/orchestration/kubernetes/overlay/prod/kustomization.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if regexp.MustCompile(`(?m)^\s+(?:digest|newTag):`).Match(manifest) {
		t.Fatal("production overlay must not check in fake digest or mutable tag substitutions")
	}
	if bytes.Contains(manifest, []byte("registry.example.com")) || bytes.Contains(manifest, []byte("sha256:0000")) {
		t.Fatal("production overlay contains a fake release image identity")
	}
	workflow, err := os.ReadFile(filepath.FromSlash(".github/workflows/verify.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	for _, contract := range []*regexp.Regexp{
		regexp.MustCompile(`packages:\s+write`),
		regexp.MustCompile(`encore build docker --push --config`),
		regexp.MustCompile(`eve-trade-encore-backend`),
		regexp.MustCompile(`eve-trade-trade-settlement`),
		regexp.MustCompile(`eve-trade-quilkin`),
		regexp.MustCompile(`release-image-lock\.json`),
		regexp.MustCompile(`render_release_kubernetes\.py`),
		regexp.MustCompile(`verify_rendered_kubernetes\.py release-kubernetes\.yaml`),
	} {
		if !contract.Match(workflow) {
			t.Fatalf("release workflow is missing immutable-image contract %s", contract)
		}
	}
}
