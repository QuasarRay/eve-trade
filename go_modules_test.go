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
	digestPattern := regexp.MustCompile(`(?m)^\s+digest:\s+sha256:[0-9a-f]{64}\s*$`)
	if count := len(digestPattern.FindAll(manifest, -1)); count != 3 {
		t.Fatalf("production overlay digest templates = %d, want 3", count)
	}
	if regexp.MustCompile(`(?m)^\s+newTag:`).Match(manifest) {
		t.Fatal("production overlay contains a mutable newTag entry")
	}
	pipeline, err := os.ReadFile(filepath.FromSlash("ci-cd/pipeline.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, contract := range []*regexp.Regexp{
		regexp.MustCompile(`encore build docker`),
		regexp.MustCompile(`encore-backend`),
		regexp.MustCompile(`sha256:[0-9a-f]{64}`),
	} {
		if !contract.Match(pipeline) {
			t.Fatalf("deployment pipeline is missing immutable-image contract %s", contract)
		}
	}
}
