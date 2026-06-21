package rabbitmqsettlement

import (
	"context"
	"errors"
	"fmt"

	amqp "github.com/rabbitmq/amqp091-go"
)

var (
	errPublishNotConfirmed = errors.New("rabbitmq publish was not confirmed")
	errPublishReturned     = errors.New("rabbitmq publish returned")
)

type publishReturnedError struct {
	exchange   string
	routingKey string
	replyCode  uint16
	replyText  string
}

func (e publishReturnedError) Error() string {
	return fmt.Sprintf(
		"rabbitmq publish returned: exchange=%q routing_key=%q reply_code=%d reply_text=%q",
		e.exchange,
		e.routingKey,
		e.replyCode,
		e.replyText,
	)
}

func (e publishReturnedError) Unwrap() error {
	return errPublishReturned
}

func isPublishReturned(err error) bool {
	return errors.Is(err, errPublishReturned)
}

type publisherConfirmations struct {
	confirms <-chan amqp.Confirmation
	returns  <-chan amqp.Return
}

func enablePublisherConfirms(channel *amqp.Channel) (publisherConfirmations, error) {
	if err := channel.Confirm(false); err != nil {
		return publisherConfirmations{}, fmt.Errorf("enable publisher confirms: %w", err)
	}
	return publisherConfirmations{
		confirms: channel.NotifyPublish(make(chan amqp.Confirmation, 64)),
		returns:  channel.NotifyReturn(make(chan amqp.Return, 64)),
	}, nil
}

func publishConfirmed(
	ctx context.Context,
	channel *amqp.Channel,
	confirmations publisherConfirmations,
	exchange string,
	routingKey string,
	mandatory bool,
	message amqp.Publishing,
) error {
	if err := channel.PublishWithContext(ctx, exchange, routingKey, mandatory, false, message); err != nil {
		return err
	}

	for {
		select {
		case returned, ok := <-confirmations.returns:
			if !ok {
				return errors.New("rabbitmq publisher return channel closed")
			}
			if !samePublishedMessage(returned, message) {
				continue
			}
			confirmErr := waitForPublishConfirmation(ctx, confirmations.confirms)
			returnedErr := publishReturnedError{
				exchange:   returned.Exchange,
				routingKey: returned.RoutingKey,
				replyCode:  returned.ReplyCode,
				replyText:  returned.ReplyText,
			}
			if confirmErr != nil {
				return errors.Join(returnedErr, confirmErr)
			}
			return returnedErr
		case confirmation, ok := <-confirmations.confirms:
			if !ok {
				return errors.New("rabbitmq publisher confirmation channel closed")
			}
			if !confirmation.Ack {
				return errPublishNotConfirmed
			}
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}
}

func waitForPublishConfirmation(ctx context.Context, confirms <-chan amqp.Confirmation) error {
	select {
	case confirmation, ok := <-confirms:
		if !ok {
			return errors.New("rabbitmq publisher confirmation channel closed")
		}
		if !confirmation.Ack {
			return errPublishNotConfirmed
		}
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func samePublishedMessage(returned amqp.Return, message amqp.Publishing) bool {
	if message.CorrelationId != "" && returned.CorrelationId != message.CorrelationId {
		return false
	}
	if message.MessageId != "" && returned.MessageId != message.MessageId {
		return false
	}
	return true
}
