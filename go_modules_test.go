package evetrade_test

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
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
		{
			"distributed-backend/src/trade-settlement/migrations/0002_merge_item_stack_constraints.sql",
			"distributed-backend/orchestration/kubernetes/base/migrations/0002_merge_item_stack_constraints.sql",
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

func TestKubernetesManifestsAvoidMutableProductionTags(t *testing.T) {
	root := filepath.FromSlash("distributed-backend/orchestration/kubernetes")
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || !(strings.HasSuffix(path, ".yaml") || strings.HasSuffix(path, ".yml")) {
			return nil
		}
		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		text := string(content)
		for _, forbidden := range []string{":latest", "newTag: latest", "newTag: prod"} {
			if strings.Contains(text, forbidden) {
				t.Fatalf("%s contains mutable image marker %q", path, forbidden)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
