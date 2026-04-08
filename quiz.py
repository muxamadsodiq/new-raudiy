VOWELS = set("aeiou")


def find_double_vowel_words(words):
    matches = {}
    for word in words:
        previous = ""
        for char in word.lower():
            if char == previous and char in VOWELS:
                matches[word] = char
                break
            previous = char
    return matches


with open("quiz.txt", "r", encoding="utf-8") as file:
    words = file.read().split()

print(find_double_vowel_words(words))
