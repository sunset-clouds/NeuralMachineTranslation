import json
import os
from parse import parse_config
import sys
from tensorboardX import SummaryWriter
from time import time
import torch
import torch.nn as nn
import torch.optim as optim

SOS = 1
EOS = 2

train_iter = [
    ([SOS, 3, 9, 4, 5, 10, EOS], [SOS, 9, 8, 6, 3, EOS]),
    ([SOS, 11, 13, 5, 4, 7, 14, 15, EOS], [SOS, 99, 6, 123, 65, 900, 11, EOS]),
]


def main():
    args = sys.argv
    if len(args) < 2:
        config_path = 'configs/default.json'
    else:
        config_path = args[1]
    with open(config_path, 'r') as f:
        config = json.load(f)
    parsed_config = parse_config(config)
    main_path = os.path.dirname(os.path.realpath(__file__))
    name = parsed_config.get('name')
    writer_path = get_or_create_dir(main_path, f'.logs/{name}')
    # TODO: call data loader here
    train(train_iter, train_iter, writer_path, parsed_config)


def train(train_iter, val_iter, writer_path, parsed_config):
    writer_train_path = get_or_create_dir(writer_path, 'train')
    writer_val_path = get_or_create_dir(writer_path, 'val')
    writer_train = SummaryWriter(log_dir=writer_train_path)
    writer_val = SummaryWriter(log_dir=writer_val_path)
    epochs = parsed_config.get('epochs')
    loss_fn = parsed_config.get('loss_fn')
    encoder, decoder = parsed_config['model']
    encoder_optimizer, decoder_optimizer = parsed_config['optimizer']
    training = parsed_config.get('training')
    eval_every = training.get('eval_every')
    sample_every = training.get('sample_every')
    step = 1
    for epoch in range(epochs):
        for i, train_pair in enumerate(train_iter):
            loss = train_sentence_pair(encoder, decoder, encoder_optimizer, decoder_optimizer, loss_fn, train_pair)

            timestamp = time()
            writer_train.add_scalar('loss', loss, step, timestamp)

            if (i + 1) % eval_every == 0:
                val_losses = 0
                val_lengths = 0
                for val_pair in val_iter:
                    val_loss, _ = evaluate_sentence_pair(encoder, decoder, loss_fn, val_pair)
                    val_losses += val_loss
                    val_lengths += 1
                val_loss = val_losses / val_lengths
                writer_val.add_scalar('loss', val_loss, step, timestamp)

            if (i + 1) % sample_every == 0:
                # TODO: sample and translate random sentences and log them to tensorboard
                pass

            step += 1


def train_sentence_pair(encoder, decoder, encoder_optimizer, decoder_optimizer, loss_fn, pair):
    encoder.train()
    decoder.train()

    loss = 0
    source_sentence, target_sentence = pair
    encoder_hidden = encoder.init_hidden()
    source_sentence = torch.LongTensor(source_sentence)
    source_sentence_length = source_sentence.size(0)
    source_hiddens = torch.zeros(source_sentence_length, encoder.hidden_size)
    target_sentence = torch.LongTensor(target_sentence)
    target_sentence_length = target_sentence.size(0)

    for i in range(source_sentence_length):
        encoder_output, encoder_hidden = encoder(source_sentence[i], encoder_hidden)
        source_hiddens[i] = encoder_output[0, 0]

    decoder_input = torch.LongTensor([[SOS]])
    decoder_hidden = encoder_hidden
    context = encoder_hidden[0]

    for i in range(target_sentence_length):
        y, context, decoder_hidden = decoder(source_sentence_length, source_hiddens, decoder_input, context, decoder_hidden)
        context = context.unsqueeze(0)
        topv, topi = y.topk(1)
        decoder_input = topi.detach()
        target = target_sentence[i].view(1)
        loss += loss_fn(y, target)
        if decoder_input.item() == EOS:
            break

    encoder_optimizer.zero_grad()
    decoder_optimizer.zero_grad()
    loss.backward()
    encoder_optimizer.step()
    decoder_optimizer.step()

    return loss


def evaluate_sentence_pair(encoder, decoder, loss_fn, pair):
    with torch.no_grad():
        encoder.eval()
        decoder.eval()

        loss = 0
        source_sentence, target_sentence = pair
        encoder_hidden = encoder.init_hidden()
        source_sentence = torch.LongTensor(source_sentence)
        source_sentence_length = source_sentence.size(0)
        source_hiddens = torch.zeros(source_sentence_length, encoder.hidden_size)
        target_sentence = torch.LongTensor(target_sentence)
        target_sentence_length = target_sentence.size(0)

        for i in range(source_sentence_length):
            encoder_output, encoder_hidden = encoder(source_sentence[i], encoder_hidden)
            source_hiddens[i] = encoder_output[0, 0]

        decoded_words = []
        decoder_input = torch.LongTensor([[SOS]])
        decoder_hidden = encoder_hidden
        context = encoder_hidden[0]

        max_length = 3 * target_sentence_length
        i = 0
        while True:
            y, context, decoder_hidden = decoder(source_sentence_length, source_hiddens, decoder_input, context, decoder_hidden)
            context = context.unsqueeze(0)
            topv, topi = y.topk(1)
            decoder_input = topi
            decoded_word = topi.item()
            if i < target_sentence_length:
                target = target_sentence[i].view(1)
                loss += loss_fn(y, target)
            if decoded_word == EOS:
                break
            decoded_words.append(decoded_word)
            if (i + 1) > max_length:
                break
            i += 1

        return loss, decoded_words


def get_or_create_dir(base_path, dir_name):
    out_directory = os.path.join(base_path, dir_name)
    if not os.path.exists(out_directory):
        os.makedirs(out_directory)
    return out_directory


if __name__ == '__main__':
    main()
