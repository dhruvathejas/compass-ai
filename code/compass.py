import math
import random

class algos:
    @staticmethod
    def sigmoid(x):
        x = max(min(x, 500), -500)  # clamp to avoid math.exp overflow
        return 1 / (1 + math.exp(-x))

    @staticmethod
    def init_weights(n_out, n_in):
        return [[random.uniform(-1, 1) for _ in range(n_in + 1)] for _ in range(n_out)]

    @staticmethod
    def layer_forward(weights, inputs):
        if weights and weights[0] and isinstance(weights[0][0], list):
            raise TypeError("weights are nested too deep; pass w_hidden or w_out, not the train() tuple")
        outputs = []
        for row in weights:
            total = sum(w * x for w, x in zip(row, inputs)) + row[-1]
            outputs.append(algos.sigmoid(total))
        return outputs

    @staticmethod
    def forward(inputs, w_hidden, w_out):
        hidden = algos.layer_forward(w_hidden, inputs)
        output = algos.layer_forward(w_out, hidden)
        return hidden, output

    @staticmethod
    def encode_text(text, length):
        raw = list(text.encode("utf-8"))[:length]
        raw += [0] * (length - len(raw))  # pad to fixed length
        return [b / 255 for b in raw]     # scale bytes to 0..1

    @staticmethod
    def train(data, hidden_size=32, lr=1.0, epochs=20000):
        # data: list of (input_text, reply_text) pairs
        in_len = max(len(x.encode("utf-8")) for x, _ in data)
        out_len = max(len(y.encode("utf-8")) for _, y in data)

        samples = [
            (algos.encode_text(x, in_len), algos.encode_text(y, out_len))
            for x, y in data
        ]

        w_hidden = algos.init_weights(hidden_size, in_len)
        w_out = algos.init_weights(out_len, hidden_size)

        for epoch in range(epochs):
            total_loss = 0.0
            for inputs, targets in samples:
                hidden, output = algos.forward(inputs, w_hidden, w_out)

                out_delta = [(o - t) * o * (1 - o) for o, t in zip(output, targets)]
                total_loss += sum((o - t) ** 2 for o, t in zip(output, targets))

                hid_delta = []
                for j in range(hidden_size):
                    err = sum(out_delta[k] * w_out[k][j] for k in range(out_len))
                    hid_delta.append(err * hidden[j] * (1 - hidden[j]))

                for k in range(out_len):
                    for j in range(hidden_size):
                        w_out[k][j] -= lr * out_delta[k] * hidden[j]
                    w_out[k][-1] -= lr * out_delta[k]

                for j in range(hidden_size):
                    for i in range(in_len):
                        w_hidden[j][i] -= lr * hid_delta[j] * inputs[i]
                    w_hidden[j][-1] -= lr * hid_delta[j]

            if epoch % 2000 == 0:
                print(f"epoch {epoch}, loss {total_loss:.5f}")

        return w_hidden, w_out, in_len, out_len

    @staticmethod
    def predict(text, w_hidden, w_out, in_len, out_len):
        _, output = algos.forward(algos.encode_text(text, in_len), w_hidden, w_out)
        raw = [round(o * 255) for o in output]          # the raw numbers
        clean = [max(0, min(255, b)) for b in raw]      # keep each one in 0-255
        return bytes(clean).decode("utf-8", errors="replace").replace("\ufffd", "")

if __name__ == "__main__":
    data = [
        ("hi", "hello"),
        ("hey", "hi there"),
        ("bye", "goodbye"),
        ("thanks", "you're welcome"),
        ("name", "i am a bot"),
    ]

    w_hidden, w_out, in_len, out_len = algos.train(data, hidden_size=32, lr=1.0, epochs=20000)

    print("Type something (or 'quit' to stop).")
    while True:
        user = input("you: ").strip()
        if user.lower() == "quit":
            break
        print("bot:", algos.predict(user, w_hidden, w_out, in_len, out_len))