package rabbitmqsettlement

import (
	"errors"
	"fmt"
	"testing"
)

func TestIsPublishReturnedRecognizesWrappedAndJoinedErrors(t *testing.T) {
	returnedErr := publishReturnedError{
		exchange:   "",
		routingKey: "amq.gen-reply",
		replyCode:  312,
		replyText:  "NO_ROUTE",
	}

	if !isPublishReturned(returnedErr) {
		t.Fatal("expected direct publishReturnedError to match")
	}

	if !isPublishReturned(fmt.Errorf("publish settlement reply: %w", returnedErr)) {
		t.Fatal("expected wrapped publishReturnedError to match")
	}

	if !isPublishReturned(errors.Join(returnedErr, errPublishNotConfirmed)) {
		t.Fatal("expected joined publishReturnedError to match")
	}
}

func TestIsPublishReturnedRejectsOtherPublishFailures(t *testing.T) {
	if isPublishReturned(errPublishNotConfirmed) {
		t.Fatal("not-confirmed publish failure must not be treated as an unroutable reply")
	}
}
