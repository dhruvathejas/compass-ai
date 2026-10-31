class data:
    data = [
        ("Hi!", "Hello!"),
        ("Hello!", "Hi!")
    ]

    ascii = [
        ' ', '!', '"', '#', '$', '%', '&', "'", '(', ')', '*', '+', ',', '-', '.', '/',
        '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
        ':', ';', '<', '=', '>', '?', '@',
        'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z',
        '[', '\\', ']', '^', '_', '`',
        'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
        '{', '|', '}', '~'
    ]


class algos:
    def tillten(x):
        if len(str(x)) == 2:
            return int(x)
        elif len(str(x)) == 1:
            return int("0" + str(x))
        return None

    def encode(string):
        codedval = data.ascii.index(string[0])

        for i in range(1, len(string)):
            if string[i] in data.ascii:
                codedval = int(
                    str(codedval) +
                    str(algos.tillten(data.ascii.index(string[i])))
                )

        return codedval

    def process(user):
        probabilities = []
        encodeuser = 0
        output = ""

        for i in range(len(data.data)):
            if user == data.data[i][0]:
                output = data.data[i][1]

        if output == "":
            encodeuser = algos.encode(str(user))

            for j in range(len(data.data)):
                encoded_data = algos.encode(data.data[j][1])
                encoded_user = encodeuser

                if len(data.data[j][0]) < len(user):
                    encoded_data *= 10 ** (
                        (len(user) - len(data.data[j][0])) * 2
                    )

                elif len(data.data[j][0]) > len(user):
                    encoded_user *= 10 ** (
                        (len(data.data[j][0]) - len(user)) * 2
                    )

                probabilities.append(
                    abs(encoded_data - encoded_user)
                )

        else:
            return output