package evetrade_test

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"testing"
)

func TestGoModules(t *testing.T) {
	modules := []string{
		"distributed-backend/proto",
		"distributed-backend/src/api-gateway",
		"distributed-backend/src/market",
		"distributed-backend/src/messaging",
		"distributed-backend/src/observability",
		"distributed-backend/src/settlement-worker",
	}

	root, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}

	for _, module := range modules {
		t.Run(module, func(t *testing.T) {
			cmd := exec.Command("go", "test", "./...")
			cmd.Dir = filepath.Join(root, filepath.FromSlash(module))
			cmd.Stdout = os.Stdout
			cmd.Stderr = os.Stderr
			if err := cmd.Run(); err != nil {
				t.Fatalf("go test ./... in %s: %v", module, err)
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
	if count := len(digestPattern.FindAll(manifest, -1)); count != 5 {
		t.Fatalf("production overlay digest templates = %d, want 5", count)
	}
	if regexp.MustCompile(`(?m)^\s+newTag:`).Match(manifest) {
		t.Fatal("production overlay contains a mutable newTag entry")
	}
	pipeline, err := os.ReadFile(filepath.FromSlash("ci-cd/pipeline.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, contract := range []*regexp.Regexp{
		regexp.MustCompile(`published_image_references\(required=True\)`),
		regexp.MustCompile(`@sha256:\[0-9a-f\]\{64\}`),
	} {
		if !contract.Match(pipeline) {
			t.Fatalf("deployment pipeline is missing immutable-image contract %s", contract)
		}
	}
}
